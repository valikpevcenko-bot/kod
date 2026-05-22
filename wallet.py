"""
D/W по сетям для всех CEX + подстановка сетей из блока «Контракти».
"""

from __future__ import annotations

import asyncio
import logging
import ssl
import time
from typing import Any, Callable, Coroutine, Optional

import aiohttp
import certifi

from config import credentials_for
from models import NetworkWallet, WalletStatus
from pool import get_exchange

logger = logging.getLogger(__name__)

_CACHE: dict[str, tuple[float, dict]] = {}
_BINANCE_COINS: tuple[float, list] | None = None
_CACHE_TTL = 300
_load_locks: dict[str, asyncio.Lock] = {}
_SSL = ssl.create_default_context(cafile=certifi.where())
_SESSION: aiohttp.ClientSession | None = None

_NETWORK_ALIASES: dict[str, str] = {
    "BSC": "BSC", "BEP20": "BSC", "BNB": "BSC", "BNB SMART CHAIN": "BSC",
    "BINANCE SMART CHAIN": "BSC", "BINANCE-SMART-CHAIN": "BSC",
    "ETH": "ETH", "ERC20": "ETH", "ETHEREUM": "ETH",
    "MANTLE": "MANTLE", "MATIC": "POLYGON", "POLYGON": "POLYGON",
    "ARBITRUM": "ARBITRUM", "ARB": "ARBITRUM", "OPTIMISM": "OPTIMISM",
    "AVAX": "AVAX", "AVALANCHE": "AVAX", "SOL": "SOL", "SOLANA": "SOL",
    "TRX": "TRON", "TRON": "TRON", "BASE": "BASE", "STX": "STX", "STACKS": "STX",
}

# Биржи, где без API-ключа CCXT не отдаёт D/W по сетям
_CCXT_AUTH_KEYS: dict[str, tuple[str, Optional[dict]]] = {
    "bybit": ("bybit", None),
    "okx": ("okx", {"options": {"defaultType": "spot"}}),
    "mexc": ("mexc", None),
    "bingx": ("bingx", None),
    "binance": ("binance", None),
    "bitget": ("bitget", None),
    "kucoin": ("kucoin", None),
    "htx": ("htx", None),
}


async def _session() -> aiohttp.ClientSession:
    global _SESSION
    if _SESSION is None or _SESSION.closed:
        _SESSION = aiohttp.ClientSession(
            connector=aiohttp.TCPConnector(ssl=_SSL),
            timeout=aiohttp.ClientTimeout(total=12),
            headers={"User-Agent": "Mozilla/5.0 (compatible; CryptoBot/1.0)"},
        )
    return _SESSION


async def close_wallet_session() -> None:
    global _SESSION
    if _SESSION and not _SESSION.closed:
        await _SESSION.close()
    _SESSION = None


async def _get_json(url: str, **kwargs: Any) -> Any:
    try:
        session = await _session()
        async with session.get(url, **kwargs) as resp:
            if resp.status != 200:
                return None
            return await resp.json()
    except Exception as exc:  # noqa: BLE001
        logger.debug("GET %s: %s", url[:70], exc)
        return None


def _norm_network(name: str) -> str:
    raw = str(name or "").strip().upper()
    if not raw:
        return "OTHER"
    for key, label in _NETWORK_ALIASES.items():
        if key == raw or key in raw:
            return label
    return raw.split()[0][:12]


def _dedupe(networks: list[NetworkWallet]) -> list[NetworkWallet]:
    seen: set[str] = set()
    out: list[NetworkWallet] = []
    for n in networks:
        if n.network in seen:
            continue
        seen.add(n.network)
        out.append(n)
    return out


def _wallet_from_networks(networks: list[NetworkWallet]) -> WalletStatus:
    networks = _dedupe(networks)
    note: Optional[str] = None
    if any(n.deposit is True for n in networks) and any(n.deposit is False for n in networks):
        note = "Депозити відкриті"
    return WalletStatus(networks=networks, note=note)


def enrich_wallet(
    wallet: WalletStatus,
    symbol: str,
    contract_networks: list[str],
) -> WalletStatus:
    """Добавляет сети из контрактов, если биржа вернула хотя бы одну сеть с данными."""
    by_net = {n.network: n for n in wallet.networks}
    has_real = any(n.deposit is not None or n.withdraw is not None for n in wallet.networks)

    if contract_networks and has_real:
        merged: list[NetworkWallet] = []
        for net in contract_networks:
            if net in by_net and (
                by_net[net].deposit is not None or by_net[net].withdraw is not None
            ):
                merged.append(by_net[net])
        for n in wallet.networks:
            if n.network not in {x.network for x in merged}:
                if n.deposit is not None or n.withdraw is not None:
                    merged.append(n)
        wallet.networks = _dedupe(merged)

    return wallet


# --- Binance ---


async def _binance_networks(symbol: str) -> list[NetworkWallet]:
    global _BINANCE_COINS
    now = time.time()
    if _BINANCE_COINS and now - _BINANCE_COINS[0] < _CACHE_TTL:
        coins = _BINANCE_COINS[1]
    else:
        data = await _get_json(
            "https://www.binance.com/bapi/capital/v1/public/capital/getNetworkCoinAll",
            timeout=aiohttp.ClientTimeout(total=25),
        )
        if not isinstance(data, dict):
            return []
        coins = data.get("data") or []
        _BINANCE_COINS = (time.time(), coins)

    for item in coins:
        if str(item.get("coin", "")).upper() != symbol:
            continue
        rows: list[NetworkWallet] = []
        for net in item.get("networkList") or []:
            rows.append(
                NetworkWallet(
                    network=_norm_network(net.get("network") or net.get("name") or ""),
                    deposit=bool(net.get("depositEnable")) if "depositEnable" in net else None,
                    withdraw=bool(net.get("withdrawEnable")) if "withdrawEnable" in net else None,
                )
            )
        return rows
    return []


# --- Gate ---


async def _gate_networks(symbol: str) -> list[NetworkWallet]:
    data = await _get_json(
        "https://api.gateio.ws/api/v4/wallet/currency_chains",
        params={"currency": symbol},
    )
    if not isinstance(data, list):
        return []
    return [
        NetworkWallet(
            network=_norm_network(c.get("chain") or c.get("name_en") or ""),
            deposit=not bool(c.get("is_deposit_disabled")),
            withdraw=not bool(c.get("is_withdraw_disabled")),
        )
        for c in data
    ]


# --- KuCoin ---


async def _kucoin_networks(symbol: str) -> list[NetworkWallet]:
    data = await _get_json(f"https://api.kucoin.com/api/v2/currencies/{symbol}")
    if not isinstance(data, dict) or data.get("code") != "200000":
        return []
    row = data.get("data") or {}
    rows: list[NetworkWallet] = []
    for ch in row.get("chains") or []:
        rows.append(
            NetworkWallet(
                network=_norm_network(ch.get("chainName") or ch.get("chain") or ""),
                deposit=bool(ch.get("isDepositEnabled")) if "isDepositEnabled" in ch else None,
                withdraw=bool(ch.get("isWithdrawEnabled")) if "isWithdrawEnabled" in ch else None,
            )
        )
    if not rows and "isDepositEnabled" in row:
        rows.append(
            NetworkWallet(
                network=_norm_network(row.get("fullName") or symbol),
                deposit=bool(row.get("isDepositEnabled")),
                withdraw=bool(row.get("isWithdrawEnabled")),
            )
        )
    return rows


# --- Bitget ---


async def _bitget_networks(symbol: str) -> list[NetworkWallet]:
    data = await _get_json(
        "https://api.bitget.com/api/v2/spot/public/coins",
        params={"coin": symbol},
    )
    if not isinstance(data, dict):
        return []
    rows: list[NetworkWallet] = []
    for item in data.get("data") or []:
        for ch in item.get("chains") or []:
            rows.append(
                NetworkWallet(
                    network=_norm_network(ch.get("chain") or ""),
                    deposit=str(ch.get("rechargeable", "")).lower() == "true",
                    withdraw=str(ch.get("withdrawable", "")).lower() == "true",
                )
            )
    return rows


# --- HTX ---


async def _htx_networks(symbol: str) -> list[NetworkWallet]:
    data = await _get_json(
        "https://api.huobi.pro/v2/reference/currencies",
        params={"currency": symbol.lower()},
    )
    if not isinstance(data, dict):
        return []
    rows: list[NetworkWallet] = []
    for block in data.get("data") or []:
        for ch in block.get("chains") or []:
            rows.append(
                NetworkWallet(
                    network=_norm_network(ch.get("displayName") or ch.get("chain") or ""),
                    deposit=ch.get("depositStatus") != "suspend" if ch.get("depositStatus") else None,
                    withdraw=ch.get("withdrawStatus") != "suspend" if ch.get("withdrawStatus") else None,
                )
            )
    return rows


# --- MEXC (публичный список монет) ---


_MEXC_COINS: tuple[float, list] | None = None


async def _mexc_networks(symbol: str) -> list[NetworkWallet]:
    global _MEXC_COINS
    now = time.time()
    if not _MEXC_COINS or now - _MEXC_COINS[0] > _CACHE_TTL:
        data = await _get_json("https://api.mexc.com/api/v3/capital/config/getall")
        # без ключа 400 — пробуем ccxt
        if isinstance(data, list):
            _MEXC_COINS = (time.time(), data)
        else:
            _MEXC_COINS = (time.time(), [])

    for item in _MEXC_COINS[1]:
        if str(item.get("coin", "")).upper() != symbol:
            continue
        rows: list[NetworkWallet] = []
        for net in item.get("networkList") or []:
            rows.append(
                NetworkWallet(
                    network=_norm_network(net.get("network") or ""),
                    deposit=bool(net.get("depositEnable")) if "depositEnable" in net else None,
                    withdraw=bool(net.get("withdrawEnable")) if "withdrawEnable" in net else None,
                )
            )
        return rows

    try:
        ex = await get_exchange("mexc")
        currencies = await asyncio.wait_for(ex.fetch_currencies(), timeout=15)
        cur = currencies.get(symbol) or currencies.get(symbol.upper()) or {}
        return _networks_from_ccxt(cur)
    except Exception:
        return []


# --- CCXT fallback ---


def _cache_key(class_name: str, extra: Optional[dict]) -> str:
    if not extra:
        return class_name
    opts = extra.get("options") or {}
    return f"{class_name}:{opts.get('defaultType', 'default')}"


async def _load_currencies(class_name: str, extra: Optional[dict]) -> dict:
    key = _cache_key(class_name, extra)
    cached = _CACHE.get(key)
    if cached and time.time() - cached[0] < _CACHE_TTL:
        return cached[1]

    if key not in _load_locks:
        _load_locks[key] = asyncio.Lock()

    async with _load_locks[key]:
        cached = _CACHE.get(key)
        if cached and time.time() - cached[0] < _CACHE_TTL:
            return cached[1]
        try:
            ex = await get_exchange(class_name, extra)
            data = await asyncio.wait_for(ex.fetch_currencies(), timeout=20)
            if not isinstance(data, dict):
                data = {}
        except Exception as exc:  # noqa: BLE001
            logger.debug("ccxt currencies %s: %s", class_name, exc)
            data = {}
        _CACHE[key] = (time.time(), data)
        return data


def _networks_from_ccxt(cur: dict) -> list[NetworkWallet]:
    rows: list[NetworkWallet] = []
    for net_id, net in (cur.get("networks") or {}).items():
        if not isinstance(net, dict):
            continue
        dep, wdr = net.get("deposit"), net.get("withdraw")
        if dep is None and wdr is None:
            continue
        rows.append(
            NetworkWallet(
                network=_norm_network(net.get("network") or net_id),
                deposit=bool(dep) if dep is not None else None,
                withdraw=bool(wdr) if wdr is not None else None,
            )
        )
    if not rows and (cur.get("deposit") is not None or cur.get("withdraw") is not None):
        rows.append(
            NetworkWallet(
                network=_norm_network(cur.get("name") or cur.get("code") or "MAIN"),
                deposit=cur.get("deposit"),
                withdraw=cur.get("withdraw"),
            )
        )
    return rows


_PUBLIC_HANDLERS: dict[str, Callable[[str], Coroutine[Any, Any, list[NetworkWallet]]]] = {
    "binance": _binance_networks,
    "gate": _gate_networks,
    "kucoin": _kucoin_networks,
    "bitget": _bitget_networks,
    "htx": _htx_networks,
    "mexc": _mexc_networks,
}


def _has_auth(exchange_key: str) -> bool:
    mapping = {
        "bybit": "bybit",
        "okx": "okx",
        "mexc": "mexc",
        "bingx": "bingx",
        "binance": "binance",
        "bitget": "bitget",
        "kucoin": "kucoin",
        "htx": "htx",
    }
    ccxt_id = mapping.get(exchange_key)
    return bool(ccxt_id and credentials_for(ccxt_id))


async def _auth_ccxt_networks(exchange_key: str, symbol: str) -> list[NetworkWallet]:
    """CCXT fetch_currencies с API-ключом (Bybit, OKX, MEXC, …)."""
    cfg = _CCXT_AUTH_KEYS.get(exchange_key)
    if not cfg or not _has_auth(exchange_key):
        return []
    cls, extra = cfg
    currencies = await _load_currencies(cls, extra)
    cur = currencies.get(symbol) or currencies.get(symbol.upper())
    if not cur:
        return []
    return _networks_from_ccxt(cur)


async def fetch_wallet(
    exchange_key: str,
    symbol: str,
    contract_networks: Optional[list[str]] = None,
) -> WalletStatus:
    if exchange_key in ("hyperliquid", "aster"):
        return WalletStatus()

    networks: list[NetworkWallet] = []

    # С ключом — сначала CCXT (полные D/W по сетям)
    auth_rows = await _auth_ccxt_networks(exchange_key, symbol)
    if auth_rows:
        networks = auth_rows

    if not networks:
        handler = _PUBLIC_HANDLERS.get(exchange_key)
        if handler:
            networks = await handler(symbol)

    if not networks and exchange_key in _CCXT_AUTH_KEYS:
        networks = await _auth_ccxt_networks(exchange_key, symbol)

    if not networks and exchange_key == "binance":
        networks = await _binance_networks(symbol)

    wallet = _wallet_from_networks(networks)
    return enrich_wallet(wallet, symbol, contract_networks or [])


async def warmup_cache() -> None:
    await _binance_networks("BTC")
    tasks = [
        _load_currencies(cls, extra)
        for cls, extra in _CCXT_AUTH_KEYS.values()
        if credentials_for(cls)
    ]
    await asyncio.gather(*tasks, return_exceptions=True)
