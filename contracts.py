"""
Контракты только с бирж (те же, что в ссылках и D/W).
Один адрес на сеть — по совпадению данных Binance, Bitget, Gate, KuCoin и др.
"""

from __future__ import annotations

import asyncio
import logging
import re
import ssl
import time
from collections import Counter, defaultdict
from typing import Optional

import aiohttp
import certifi

from config import CMC_API_KEY
from models import ContractInfo
from pool import get_exchange

logger = logging.getLogger(__name__)

_SSL = ssl.create_default_context(cafile=certifi.where())
_CACHE: dict[str, tuple[float, list[ContractInfo]]] = {}
_CACHE_TTL = 600
# Быстрый путь: отдаём последний кэш, не ждём тяжёлый Binance bapi
_MEM: dict[str, tuple[float, list[ContractInfo]]] = {}
_MEM_TTL = 600
_BINANCE_COINS: tuple[float, list] | None = None

_EVM_RE = re.compile(r"^0x[a-fA-F0-9]{40}$")

_CHAIN_ALIASES: dict[str, str] = {
    "BSC": "BSC", "BEP20": "BSC", "BNB": "BSC", "BNB SMART CHAIN": "BSC",
    "ETH": "ETH", "ERC20": "ETH", "ETHEREUM": "ETH",
    "MANTLE": "MANTLE", "MATIC": "POLYGON", "POLYGON": "POLYGON",
    "ARBITRUM": "ARBITRUM", "ARB": "ARBITRUM", "OPTIMISM": "OPTIMISM",
    "AVAX": "AVAX", "AVALANCHE": "AVAX", "SOL": "SOL", "SOLANA": "SOL",
    "TRX": "TRON", "TRON": "TRON", "BASE": "BASE", "STX": "STX", "STACKS": "STX",
}
# Приоритет при разных адресах на одной сети
_EXCHANGE_PRIORITY = (
    "binance", "bitget", "gate", "kucoin", "mexc", "okx", "bybit", "bingx", "htx",
)

_CCXT_EXCHANGE = {
    "bybit": ("bybit", None),
    "okx": ("okx", {"options": {"defaultType": "spot"}}),
    "mexc": ("mexc", None),
    "bingx": ("bingx", None),
    "htx": ("htx", None),
}

_NETWORK_ORDER = {"BSC": 0, "ETH": 1, "BASE": 2, "MANTLE": 3, "ARBITRUM": 4, "STX": 5}


def _norm_chain(name: str) -> str:
    raw = str(name or "").strip().upper()
    if not raw:
        return "OTHER"
    for key, label in _CHAIN_ALIASES.items():
        if key == raw or key in raw:
            return label
    return raw.split()[0][:12]


def _is_evm(addr: str) -> bool:
    return bool(_EVM_RE.match(addr.strip()))


def _is_native(addr: str) -> bool:
    return addr.strip().lower() in ("native", "") or addr.lower().startswith("native")


async def _http_get_json(
    url: str,
    params: Optional[dict] = None,
    headers: Optional[dict] = None,
) -> dict | list | None:
    try:
        async with aiohttp.ClientSession(
            connector=aiohttp.TCPConnector(ssl=_SSL),
            timeout=aiohttp.ClientTimeout(total=14),
        ) as session:
            async with session.get(url, params=params, headers=headers or {}) as resp:
                if resp.status != 200:
                    return None
                return await resp.json()
    except Exception as exc:  # noqa: BLE001
        logger.debug("contracts %s: %s", url[:60], exc)
        return None


def _consensus(rows: list[tuple[str, str, str]]) -> list[ContractInfo]:
    """
    rows: (биржа, сеть, адрес)
    Один контракт на сеть — адрес с макс. совпадений или с биржи с приоритетом.
    """
    # network -> address -> count
    votes: dict[str, Counter] = defaultdict(Counter)
    # network -> exchange -> address (для приоритета)
    by_ex: dict[str, dict[str, str]] = defaultdict(dict)
    native_votes: dict[str, int] = defaultdict(int)
    case_map: dict[str, str] = {}

    for exchange, network, address in rows:
        net = _norm_chain(network)
        addr = address.strip()
        if not addr:
            continue

        if _is_native(addr):
            native_votes[net] += 1
            continue

        if not _is_evm(addr):
            continue

        key = addr.lower()
        votes[net][key] += 1
        by_ex[net][exchange] = addr
        case_map[key] = addr

    out: list[ContractInfo] = []
    all_nets = set(votes) | set(native_votes)

    for net in all_nets:
        if net in votes and votes[net]:
            best_key, count = votes[net].most_common(1)[0]
            # Конфликт: два адреса с 1 голосом — берём с приоритетной биржи
            if len(votes[net]) > 1 and count == 1:
                chosen = None
                for ex in _EXCHANGE_PRIORITY:
                    if ex in by_ex[net]:
                        chosen = by_ex[net][ex]
                        break
                addr = chosen or case_map[best_key]
            else:
                addr = case_map[best_key]
            out.append(ContractInfo(network=net, address=addr))
        elif native_votes[net] and net not in votes:
            out.append(ContractInfo(network=net, address="native"))

    out.sort(key=lambda c: (_NETWORK_ORDER.get(c.network, 99), c.network))
    return out


# --- Binance ---


async def _binance_rows(symbol: str) -> list[tuple[str, str, str]]:
    global _BINANCE_COINS
    now = time.time()
    if not _BINANCE_COINS or now - _BINANCE_COINS[0] > _CACHE_TTL:
        data = await _http_get_json(
            "https://www.binance.com/bapi/capital/v1/public/capital/getNetworkCoinAll"
        )
        coins = (data or {}).get("data") or [] if isinstance(data, dict) else []
        _BINANCE_COINS = (time.time(), coins)

    rows: list[tuple[str, str, str]] = []
    for item in _BINANCE_COINS[1]:
        if str(item.get("coin", "")).upper() != symbol:
            continue
        for net in item.get("networkList") or []:
            label = _norm_chain(net.get("network") or net.get("name") or "")
            addr = (net.get("contractAddress") or "").strip() or "native"
            rows.append(("binance", label, addr))
        return rows
    return []


# --- Bitget ---


async def _bitget_rows(symbol: str) -> list[tuple[str, str, str]]:
    data = await _http_get_json(
        "https://api.bitget.com/api/v2/spot/public/coins",
        params={"coin": symbol},
    )
    if not isinstance(data, dict):
        return []
    rows: list[tuple[str, str, str]] = []
    for item in data.get("data") or []:
        for ch in item.get("chains") or []:
            label = _norm_chain(ch.get("chain") or "")
            addr = (ch.get("contractAddress") or "").strip() or "native"
            rows.append(("bitget", label, addr))
    return rows


# --- Gate ---


async def _gate_rows(symbol: str) -> list[tuple[str, str, str]]:
    data = await _http_get_json(
        "https://api.gateio.ws/api/v4/wallet/currency_chains",
        params={"currency": symbol},
    )
    if not isinstance(data, list):
        return []
    rows: list[tuple[str, str, str]] = []
    for ch in data:
        label = _norm_chain(ch.get("chain") or ch.get("name_en") or "")
        addr = (ch.get("contract_address") or "").strip() or "native"
        rows.append(("gate", label, addr))
    return rows


# --- KuCoin ---


async def _kucoin_rows(symbol: str) -> list[tuple[str, str, str]]:
    data = await _http_get_json(f"https://api.kucoin.com/api/v2/currencies/{symbol}")
    if not isinstance(data, dict) or data.get("code") != "200000":
        return []
    row = data.get("data") or {}
    rows: list[tuple[str, str, str]] = []
    for ch in row.get("chains") or []:
        label = _norm_chain(ch.get("chainName") or ch.get("chain") or "")
        addr = (ch.get("contractAddress") or "").strip() or "native"
        rows.append(("kucoin", label, addr))
    return rows


# --- CCXT (Bybit, OKX, MEXC, BingX, HTX) — с API-ключом в .env ---


async def _ccxt_rows(exchange_key: str, symbol: str) -> list[tuple[str, str, str]]:
    cfg = _CCXT_EXCHANGE.get(exchange_key)
    if not cfg:
        return []
    try:
        ex = await get_exchange(cfg[0], cfg[1])
        currencies = await asyncio.wait_for(ex.fetch_currencies(), timeout=20)
    except Exception as exc:  # noqa: BLE001
        logger.debug("ccxt contracts %s: %s", exchange_key, exc)
        return []

    cur = currencies.get(symbol) or currencies.get(symbol.upper())
    if not cur:
        return []

    rows: list[tuple[str, str, str]] = []
    for net_id, net in (cur.get("networks") or {}).items():
        if not isinstance(net, dict):
            continue
        label = _norm_chain(net.get("network") or net_id)
        info = net.get("info") or {}
        addr = (
            (info.get("contractAddress") or info.get("contract") or "")
            .strip()
        )
        if not addr and exchange_key == "mexc":
            addr = (info.get("contract") or "").strip()
        if not addr:
            if net.get("deposit") is not None or net.get("withdraw") is not None:
                rows.append((exchange_key, label, "native"))
            continue
        rows.append((exchange_key, label, addr))
    return rows


# --- Fallback: CoinGecko / CMC только если биржи ничего не дали ---


async def _coingecko_fallback(symbol: str) -> list[ContractInfo]:
    search = await _http_get_json(
        "https://api.coingecko.com/api/v3/search",
        params={"query": symbol},
    )
    coin_id = None
    if isinstance(search, dict):
        for coin in search.get("coins") or []:
            if str(coin.get("symbol", "")).upper() == symbol:
                coin_id = coin.get("id")
                break
    if not coin_id:
        return []

    detail = await _http_get_json(
        f"https://api.coingecko.com/api/v3/coins/{coin_id}",
        params={
            "localization": "false",
            "tickers": "false",
            "market_data": "false",
            "community_data": "false",
            "developer_data": "false",
        },
    )
    if not isinstance(detail, dict):
        return []

    out: list[ContractInfo] = []
    for pid, info in (detail.get("detail_platforms") or {}).items():
        addr = ((info or {}).get("contract_address") or "").strip()
        if _is_evm(addr):
            out.append(ContractInfo(_norm_chain(pid), addr))
    return out


async def _cmc_fallback(symbol: str) -> list[ContractInfo]:
    if not CMC_API_KEY:
        return []
    data = await _http_get_json(
        "https://pro-api.coinmarketcap.com/v2/cryptocurrency/info",
        headers={"X-CMC_PRO_API_KEY": CMC_API_KEY},
        params={"symbol": symbol},
    )
    if not isinstance(data, dict):
        return []
    raw = data.get("data") or {}
    block = raw.get(symbol) if isinstance(raw.get(symbol), dict) else None
    if not block and isinstance(raw, dict) and raw:
        block = next(iter(raw.values()), None)
    if not block:
        return []

    out: list[ContractInfo] = []
    for entry in block.get("contract_address") or []:
        addr = (entry.get("contract_address") or "").strip()
        if not _is_evm(addr):
            continue
        platform = entry.get("platform") or {}
        name = str(platform.get("name") or "")
        low = name.lower()
        label = "OTHER"
        for needle, lbl in _CHAIN_ALIASES.items():
            if needle.lower() in low:
                label = lbl
                break
        out.append(ContractInfo(label, addr))
    return out


_EXCHANGE_FETCHERS = {
    "binance": _binance_rows,
    "bitget": _bitget_rows,
    "gate": _gate_rows,
    "kucoin": _kucoin_rows,
}


def get_cached(symbol: str) -> list[ContractInfo]:
    """Мгновенно — без HTTP (для ответа за 1 сек)."""
    item = _MEM.get(symbol.upper())
    if not item:
        return []
    if time.time() - item[0] > _MEM_TTL:
        return []
    return item[1]


def _mem_store(symbol: str, contracts: list[ContractInfo]) -> None:
    if contracts:
        _MEM[symbol.upper()] = (time.time(), contracts)


async def preload_binance_coins() -> None:
    """Прогрев списка сетей Binance — контракты/D/W быстрее на первом /get."""
    await _binance_rows("BTC")


async def fetch_contracts(
    symbol: str,
    listed_on: Optional[list[str]] = None,
) -> list[ContractInfo]:
    """
    Контракты с бирж, где есть тикер.
    listed_on — ключи бирж из ответа (binance, bybit, …); если None — опрос всех.
    """
    key = symbol.upper()
    cached = _CACHE.get(key)
    if cached and time.time() - cached[0] < _CACHE_TTL:
        return cached[1]

    exchanges = listed_on or list(_EXCHANGE_FETCHERS) + list(_CCXT_EXCHANGE)

    tasks: list[tuple[str, asyncio.Task]] = []
    for ex_key in exchanges:
        if ex_key in _EXCHANGE_FETCHERS:
            tasks.append((ex_key, asyncio.create_task(_EXCHANGE_FETCHERS[ex_key](key))))
        elif ex_key in _CCXT_EXCHANGE:
            tasks.append((ex_key, asyncio.create_task(_ccxt_rows(ex_key, key))))

    all_rows: list[tuple[str, str, str]] = []
    for ex_key, task in tasks:
        try:
            result = await task
            if isinstance(result, list):
                all_rows.extend(result)
        except Exception as exc:  # noqa: BLE001
            logger.debug("contract fetch %s: %s", ex_key, exc)

    contracts = _consensus(all_rows)

    # Fallback только если биржи не дали ни одного EVM-контракта
    if not any(_is_evm(c.address) for c in contracts):
        for fb in await asyncio.gather(
            _coingecko_fallback(key),
            _cmc_fallback(key),
            return_exceptions=True,
        ):
            if isinstance(fb, list) and fb:
                contracts = fb
                break

    _CACHE[key] = (time.time(), contracts)
    _mem_store(key, contracts)
    return contracts
