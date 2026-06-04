"""CoinMarketCap Pro API client."""

from __future__ import annotations

import asyncio
import re
import time
from collections import Counter, defaultdict
from typing import Any

import structlog

from crypto_bot.config.settings import get_settings
from crypto_bot.core.http import get_http
from crypto_bot.models.market import ContractInfo

logger = structlog.get_logger(__name__)

EVM_RE = re.compile(r"^0x[a-fA-F0-9]{40}$")
CHAIN_ALIASES: dict[str, str] = {
    "BSC": "BSC",
    "BEP20": "BSC",
    "BNB": "BSC",
    "ETH": "ETH",
    "ERC20": "ETH",
    "ETHEREUM": "ETH",
    "MANTLE": "MANTLE",
    "MATIC": "POLYGON",
    "POLYGON": "POLYGON",
    "ARBITRUM": "ARBITRUM",
    "SOL": "SOL",
    "SOLANA": "SOL",
    "TRX": "TRON",
    "TRON": "TRON",
    "BASE": "BASE",
    "OPTIMISM": "OPTIMISM",
    "OP MAINNET": "OPTIMISM",
    "STX": "STX",
    "CYBER": "CYBER",
    "STARKNET": "STARKNET",
    "ZKSYNCERA": "ZKSYNCERA",
    "ZKSYNC ERA": "ZKSYNCERA",
    "ZKSYNC": "ZKSYNCERA",
    "LINEA": "LINEA",
    "SCROLL": "SCROLL",
    "WLD": "WLD",
    "WORLD CHAIN": "WLD",
}

# Только крупные L1/L2 в блоке «Контракти» (без SOPHON, KROWN, BLAST, …)
MAIN_CONTRACT_NETWORKS: frozenset[str] = frozenset(
    {
        "BSC",
        "ETH",
        "SOL",
        "BASE",
        "ARBITRUM",
        "OPTIMISM",
        "POLYGON",
        "MANTLE",
        "TRON",
        "AVAX",
        "TON",
        "APT",
        "SUI",
        "NEAR",
        "STX",
        "CYBER",
        "WLD",
        "STARKNET",
        "ZKSYNCERA",
        "LINEA",
        "SCROLL",
    }
)

_PLACEHOLDER_EVM = "0xeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee"

NETWORK_ORDER = {
    "BSC": 0,
    "ETH": 1,
    "SOL": 2,
    "BASE": 3,
    "ARBITRUM": 4,
    "OPTIMISM": 5,
    "POLYGON": 6,
    "MANTLE": 7,
    "STARKNET": 8,
    "ZKSYNCERA": 9,
    "LINEA": 10,
    "SCROLL": 11,
    "CYBER": 12,
    "WLD": 13,
    "TRON": 14,
    "AVAX": 15,
    "TON": 16,
    "APT": 17,
    "SUI": 18,
    "NEAR": 19,
    "STX": 20,
}
EXCHANGE_PRIORITY = ("binance", "bitget", "gate", "kucoin", "mexc", "okx", "bybit", "bingx")

_map_cache: dict[str, tuple[float, list[dict[str, Any]]]] = {}
_MAP_TTL = 3600


def _match_chain_alias(text: str) -> str | None:
    t = text.strip().upper()
    if not t:
        return None
    if t in CHAIN_ALIASES:
        return CHAIN_ALIASES[t]
    for key in sorted(CHAIN_ALIASES, key=len, reverse=True):
        if len(key) < 3:
            continue
        if key == "SOL" and "OPTIMISM" in t:
            continue
        if key in t:
            return CHAIN_ALIASES[key]
    return None


def norm_chain(name: str) -> str:
    raw = str(name or "").strip()
    if not raw:
        return "OTHER"
    hit = _match_chain_alias(raw)
    if hit:
        return hit
    return raw.upper().split()[0][:12]


def is_evm(addr: str) -> bool:
    return bool(EVM_RE.match(addr.strip()))


def is_native(addr: str) -> bool:
    low = addr.strip().lower()
    return low in ("native", "") or low.startswith("native")


def is_solana(addr: str) -> bool:
    a = addr.strip()
    if not a or is_native(a) or is_evm(a):
        return False
    if len(a) < 32 or len(a) > 44:
        return False
    base58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
    return all(c in base58 for c in a)


def _is_placeholder_address(addr: str) -> bool:
    return addr.strip().lower() == _PLACEHOLDER_EVM


def _pick_contract(prev: ContractInfo | None, cand: ContractInfo) -> ContractInfo:
    if prev is None:
        return cand
    if _is_placeholder_address(prev.address) and not _is_placeholder_address(cand.address):
        return cand
    if _is_placeholder_address(cand.address):
        return prev
    if is_evm(cand.address) and not is_evm(prev.address):
        return cand
    return prev


_NATIVE_CHAIN_ALIASES: dict[str, frozenset[str]] = {
    "BTC": frozenset({"BTC", "BITCOIN"}),
    "ETH": frozenset({"ETH", "ETHEREUM"}),
    "SOL": frozenset({"SOL", "SOLANA"}),
    "BNB": frozenset({"BNB", "BSC"}),
    "STX": frozenset({"STX", "STACKS"}),
}


def is_native_chain_contract(coin: str, network: str) -> bool:
    """Не показувати контракт монети в її власній L1 (SOL у Solana, BTC у Bitcoin, …)."""
    c = coin.strip().upper()
    if not c:
        return False
    net = norm_chain(network)
    if net == c:
        return True
    return net in _NATIVE_CHAIN_ALIASES.get(c, frozenset())


def filter_display_contracts(
    contracts: list[ContractInfo],
    *,
    coin: str | None = None,
) -> list[ContractInfo]:
    """Основные сети с реальным адресом; одна строка на сеть."""
    by_net: dict[str, ContractInfo] = {}
    for c in contracts:
        net = norm_chain(c.network)
        if coin and is_native_chain_contract(coin, net):
            continue
        if net not in MAIN_CONTRACT_NETWORKS:
            continue
        addr = c.address.strip()
        if not addr or is_native(addr) or _is_placeholder_address(addr):
            continue
        row = ContractInfo(network=net, address=addr)
        by_net[net] = _pick_contract(by_net.get(net), row)
    out = list(by_net.values())
    out.sort(key=lambda c: (NETWORK_ORDER.get(c.network, 99), c.network))
    return out


class CoinMarketCapClient:
    """CMC map + info endpoints."""

    def __init__(self) -> None:
        self._http = get_http()

    def _headers(self) -> dict[str, str]:
        key = get_settings().cmc_key()
        return {"X-CMC_PRO_API_KEY": key or ""}

    async def map_entries(self, symbol: str) -> list[dict[str, Any]]:
        key = get_settings().cmc_key()
        if not key:
            return []
        sym = symbol.upper()
        hit = _map_cache.get(sym)
        if hit and time.time() - hit[0] < _MAP_TTL:
            return hit[1]
        data = await self._http.get_json(
            "https://pro-api.coinmarketcap.com/v1/cryptocurrency/map",
            headers=self._headers(),
            params={"symbol": sym, "limit": 50},
            timeout=2.5,
        )
        entries: list[dict[str, Any]] = []
        if isinstance(data, dict):
            entries = [e for e in (data.get("data") or []) if isinstance(e, dict)]
        _map_cache[sym] = (time.time(), entries)
        return entries

    async def contracts_by_id(self, cmc_id: int) -> list[ContractInfo]:
        data = await self._http.get_json(
            "https://pro-api.coinmarketcap.com/v2/cryptocurrency/info",
            headers=self._headers(),
            params={"id": str(cmc_id)},
            timeout=2.5,
        )
        if not isinstance(data, dict):
            return []
        out: list[ContractInfo] = []
        seen: set[str] = set()
        for block in (data.get("data") or {}).values():
            if not isinstance(block, dict):
                continue
            for entry in block.get("contract_address") or []:
                addr = (entry.get("contract_address") or "").strip()
                if not addr:
                    continue
                platform = entry.get("platform") or {}
                label = self._platform_label(platform)
                if label in seen:
                    continue
                seen.add(label)
                if is_evm(addr) or (label == "SOL" and is_solana(addr)):
                    out.append(ContractInfo(network=label, address=addr))
        out.sort(key=lambda c: (NETWORK_ORDER.get(c.network, 99), c.network))
        return out

    @staticmethod
    def _platform_coin_text(platform: dict[str, Any]) -> str:
        coin = platform.get("coin")
        if isinstance(coin, dict):
            return str(coin.get("symbol") or coin.get("name") or "").strip()
        return str(coin or "").strip()

    def _platform_label(self, platform: dict[str, Any]) -> str:
        name = str(platform.get("name") or platform.get("symbol") or "").strip()
        if name:
            hit = _match_chain_alias(name)
            if hit:
                return hit
        coin = self._platform_coin_text(platform)
        if coin:
            hit = _match_chain_alias(coin)
            if hit:
                return hit
        return norm_chain(name) if name else "OTHER"

    async def resolve_cmc_id(
        self,
        entries: list[dict[str, Any]],
        exchange_rows: list[tuple[str, str, str]],
    ) -> int | None:
        if not entries:
            return None
        if len(entries) == 1:
            cmc_id = entries[0].get("id")
            return int(cmc_id) if cmc_id is not None else None

        addrs = {addr.lower() for _, _, addr in exchange_rows if is_evm(addr)}
        prelim = sorted(entries, key=lambda e: self._score(e, addrs), reverse=True)
        candidates = prelim[: min(4, len(prelim))]

        async def entry_score(entry: dict[str, Any]) -> tuple[int, dict[str, Any]]:
            raw_id = entry.get("id")
            if raw_id is None:
                return 0, entry
            contracts = await self.contracts_by_id(int(raw_id))
            return self._exchange_contract_score(entry, exchange_rows, contracts), entry

        scored = await asyncio.gather(*[entry_score(e) for e in candidates])
        best_score, best = max(scored, key=lambda pair: pair[0])
        if best_score <= 0:
            best = prelim[0]
        cmc_id = best.get("id")
        return int(cmc_id) if cmc_id is not None else None

    @staticmethod
    def _exchange_contract_score(
        entry: dict[str, Any],
        exchange_rows: list[tuple[str, str, str]],
        contracts: list[ContractInfo],
    ) -> int:
        """Pick listing that matches the most exchange rows (same network + address)."""
        score = 0
        if entry.get("is_active"):
            score += 10_000
        rank = entry.get("rank")
        if isinstance(rank, int) and rank > 0:
            score += max(0, 5_000 - rank)

        by_net: dict[str, str] = {}
        for c in contracts:
            net = norm_chain(c.network)
            addr = c.address.strip()
            if not addr or is_native(addr):
                continue
            if is_evm(addr):
                by_net[net] = addr.lower()
            elif net == "SOL" and is_solana(addr):
                by_net[net] = addr

        for _exchange, network, address in exchange_rows:
            net = norm_chain(network)
            addr = address.strip()
            if is_native(addr) or not addr:
                continue
            cmc_addr = by_net.get(net)
            if not cmc_addr:
                continue
            if is_evm(addr) and addr.lower() == cmc_addr:
                score += 50_000
            elif net == "SOL" and is_solana(addr) and addr == cmc_addr:
                score += 50_000
        return score

    def _score(self, entry: dict[str, Any], exchange_addrs: set[str]) -> int:
        score = 0
        if entry.get("is_active"):
            score += 10_000
        rank = entry.get("rank")
        if isinstance(rank, int) and rank > 0:
            score += max(0, 5_000 - rank)
        plat = entry.get("platform") or {}
        addr = (plat.get("token_address") or "").strip().lower()
        if addr and addr in exchange_addrs:
            score += 100_000
        return score

    def consensus(self, rows: list[tuple[str, str, str]]) -> list[ContractInfo]:
        votes: dict[str, Counter[str]] = defaultdict(Counter)
        by_ex: dict[str, dict[str, str]] = defaultdict(dict)
        native_votes: dict[str, int] = defaultdict(int)
        case_map: dict[str, str] = {}

        for exchange, network, address in rows:
            net = norm_chain(network)
            addr = address.strip()
            if not addr:
                continue
            if is_native(addr):
                native_votes[net] += 1
                continue
            if is_evm(addr):
                key = addr.lower()
            elif net == "SOL" and is_solana(addr):
                key = addr
            else:
                continue
            votes[net][key] += 1
            by_ex[net][exchange] = addr
            case_map[key] = addr

        out: list[ContractInfo] = []
        for net in set(votes) | set(native_votes):
            if net in votes and votes[net]:
                best_key, count = votes[net].most_common(1)[0]
                if len(votes[net]) > 1 and count == 1:
                    chosen = None
                    for ex in EXCHANGE_PRIORITY:
                        if ex in by_ex[net]:
                            chosen = by_ex[net][ex]
                            break
                    addr = chosen or case_map[best_key]
                else:
                    addr = case_map[best_key]
                out.append(ContractInfo(network=net, address=addr))
            elif native_votes[net] and net not in votes:
                out.append(ContractInfo(network=net, address="native"))
        out.sort(key=lambda c: (NETWORK_ORDER.get(c.network, 99), c.network))
        return out
