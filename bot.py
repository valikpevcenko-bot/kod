#!/usr/bin/env python3
"""
Crypto Telegram Bot — один файл.
/get TICKER — цены ~1 сек, контракты и D/W догружаются в то же сообщение.
"""

from __future__ import annotations

import asyncio
import html
import logging
import os
import re
import ssl
import sys
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Any, Callable, Coroutine, Iterable, Optional

import aiohttp
import ccxt.async_support as ccxt
import certifi
from aiogram import Bot, Dispatcher, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandObject
from aiogram.types import Message
from dotenv import load_dotenv

_ENV_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
load_dotenv(_ENV_FILE, override=True)

BOT_TOKEN: str | None = os.getenv("BOT_TOKEN")
DEFAULT_QUOTE: str = os.getenv("DEFAULT_QUOTE", "USDT")
CMC_API_KEY: str | None = os.getenv("CMC_API_KEY")


def _api_creds(key: str, secret: str, password: str | None = None) -> dict[str, str]:
    """Собирает dict для CCXT, если заданы key и secret."""
    api_key = os.getenv(key)
    api_secret = os.getenv(secret)
    if not api_key or not api_secret:
        return {}
    out: dict[str, str] = {"apiKey": api_key, "secret": api_secret}
    if password:
        pwd = os.getenv(password)
        if pwd:
            out["password"] = pwd
    return out


def credentials_for(ccxt_id: str) -> dict[str, Any]:
    """
    Read-only API ключи бирж (для D/W).
    ccxt_id: binance | bybit | okx | mexc | bingx | bitget | kucoin | htx
    """
    mapping = {
        "binance": _api_creds("BINANCE_API_KEY", "BINANCE_API_SECRET"),
        "bybit": _api_creds("BYBIT_API_KEY", "BYBIT_API_SECRET"),
        "okx": _api_creds("OKX_API_KEY", "OKX_API_SECRET", "OKX_API_PASSPHRASE"),
        "mexc": _api_creds("MEXC_API_KEY", "MEXC_API_SECRET"),
        "bingx": _api_creds("BINGX_API_KEY", "BINGX_API_SECRET"),
        "bitget": _api_creds("BITGET_API_KEY", "BITGET_API_SECRET", "BITGET_API_PASSPHRASE"),
        "kucoin": _api_creds("KUCOIN_API_KEY", "KUCOIN_API_SECRET", "KUCOIN_API_PASSPHRASE"),
        "htx": _api_creds("HTX_API_KEY", "HTX_API_SECRET"),
        "huobi": _api_creds("HTX_API_KEY", "HTX_API_SECRET"),
    }
    return mapping.get(ccxt_id, {})


def require_token() -> str:
    token = (BOT_TOKEN or "").strip().strip('"').strip("'")
    bad = (
        not token,
        "ВСТАВЬ" in token.upper() or "YOUR" in token.upper() or "TOKEN" in token.upper() and ":" not in token,
        len(token) < 30,
    )
    if bad[0] or bad[1] or bad[2]:
        print(
            "❌ Неверный BOT_TOKEN в файле .env\n\n"
            "   1. Открой Telegram → @BotFather\n"
            "   2. /newbot или /token → скопируй токен\n"
            "   3. Вставь в .env одной строкой:\n"
            "      BOT_TOKEN=123456789:ABCdef...\n\n"
            f"   Файл: {_ENV_FILE}",
            file=sys.stderr,
        )
        sys.exit(1)
    return token

# ========================================================================
# MODELS
# ========================================================================



@dataclass
class MarketTicker:
    price: float
    url: Optional[str] = None


@dataclass
class NetworkWallet:
    """D/W по одной сети (BSC, ETH, …)."""

    network: str
    deposit: Optional[bool] = None
    withdraw: Optional[bool] = None


@dataclass
class WalletStatus:
    networks: list[NetworkWallet] = field(default_factory=list)
    note: Optional[str] = None  # «Депозити відкриті»


@dataclass
class ContractInfo:
    network: str
    address: str


@dataclass
class ExchangeSnapshot:
    key: str
    name: str
    spot: Optional[MarketTicker] = None
    futures: Optional[MarketTicker] = None
    wallet: Optional[WalletStatus] = None
    futures_only: bool = False

    @property
    def has_data(self) -> bool:
        return self.spot is not None or self.futures is not None

# ========================================================================
# TICKER_PARSER
# ========================================================================



# Популярные котируемые валюты (от длинных к коротким, чтобы корректно отрезать суффикс)
_QUOTES = ("USDT", "USDC", "BUSD", "USD", "BTC", "ETH", "EUR")


def parse_ticker(raw: str, default_quote: str = DEFAULT_QUOTE) -> tuple[str, str]:
    """
    Преобразует ввод пользователя в пару (base, quote).

    Примеры:
        BTCUSDT  -> (BTC, USDT)
        BTC/USDT -> (BTC, USDT)
        btc      -> (BTC, USDT)  # quote из DEFAULT_QUOTE
    """
    text = raw.strip().upper()
    if not text:
        raise ValueError("Пустой тикер")

    # Формат BASE/QUOTE
    if "/" in text:
        base, quote = text.split("/", 1)
        base, quote = base.strip(), quote.strip()
        if not base or not quote:
            raise ValueError("Неверный формат. Пример: BTCUSDT или BTC/USDT")
        return base, quote

    # Только база: BTC
    if re.fullmatch(r"[A-Z0-9]{1,15}", text):
        for quote in _QUOTES:
            if text.endswith(quote) and len(text) > len(quote):
                base = text[: -len(quote)]
                if base:
                    return base, quote
        # Не нашли суффикс — считаем, что это только базовый актив
        return text, default_quote.upper()

    raise ValueError("Тикер может содержать только буквы и цифры")


def to_ccxt_spot_symbol(base: str, quote: str) -> str:
    """Символ CCXT для спота: BTC/USDT."""
    return f"{base}/{quote}"


def to_ccxt_swap_symbol(base: str, quote: str) -> str:
    """Символ CCXT для USDT perpetual: BTC/USDT:USDT."""
    return f"{base}/{quote}:{quote}"

# ========================================================================
# LINKS
# ========================================================================



def spot_url(exchange_key: str, base: str, quote: str) -> Optional[str]:
    b, q = base.upper(), quote.upper()
    bl, ql = base.lower(), quote.lower()
    templates = {
        "binance": f"https://www.binance.com/en/trade/{b}_{q}",
        "bybit": f"https://www.bybit.com/trade/spot/{b}/{q}",
        "gate": f"https://www.gate.io/trade/{b}_{q}",
        "mexc": f"https://www.mexc.com/exchange/{b}_{q}",
        "bitget": f"https://www.bitget.com/spot/{b}{q}",
        "okx": f"https://www.okx.com/trade-spot/{bl}-{ql}",
        "kucoin": f"https://www.kucoin.com/trade/{b}-{q}",
        "bingx": f"https://bingx.com/en-us/spot/{b}{q}",
        "htx": f"https://www.htx.com/trade/{bl}_{ql}",
    }
    return templates.get(exchange_key)


def futures_url(exchange_key: str, base: str, quote: str) -> Optional[str]:
    b, q = base.upper(), quote.upper()
    bl, ql = base.lower(), quote.lower()
    templates = {
        "binance": f"https://www.binance.com/en/futures/{b}{q}",
        "bybit": f"https://www.bybit.com/trade/usdt/{b}{q}",
        "gate": f"https://www.gate.io/futures/{q}/{b}_{q}",
        "mexc": f"https://www.mexc.com/futures/{b}_{q}",
        "bitget": f"https://www.bitget.com/futures/usdt/{b}{q}",
        "okx": f"https://www.okx.com/trade-swap/{bl}-{ql}-swap",
        "kucoin": f"https://www.kucoin.com/futures/trade/{b}{q}M",
        "bingx": f"https://bingx.com/en-us/perpetual/{b}-{q}",
        "htx": f"https://www.htx.com/futures/linear_swap/exchange#contract_code={b}-{q}",
        "aster": f"https://www.asterdex.com/en/futures/{b}{q}",
        "hyperliquid": f"https://app.hyperliquid.xyz/trade/{b}",
    }
    return templates.get(exchange_key)

# ========================================================================
# RESPONSE_CACHE
# ========================================================================




# Секунды: свежий ответ отдаём мгновенно
REPORT_TTL = 25


@dataclass
class CachedReport:
    text: str
    snapshots: list[ExchangeSnapshot]
    contracts: list[ContractInfo]
    ts: float
    # False = только цены; True = контракты + D/W подтянуты
    complete: bool = False


_store: dict[str, CachedReport] = {}


def _key(base: str, quote: str) -> str:
    return f"{base.upper()}:{quote.upper()}"


def report_cache_get(base: str, quote: str) -> Optional[CachedReport]:
    item = _store.get(_key(base, quote))
    if not item:
        return None
    if time.time() - item.ts > REPORT_TTL:
        return None
    return item


def report_cache_set(
    base: str,
    quote: str,
    text: str,
    snapshots: list[ExchangeSnapshot],
    contracts: list[ContractInfo],
    *,
    complete: bool = False,
) -> None:
    _store[_key(base, quote)] = CachedReport(
        text=text,
        snapshots=snapshots,
        contracts=contracts,
        ts=time.time(),
        complete=complete,
    )

# ========================================================================
# WALLET_CACHE
# ========================================================================




TTL = 300
_store: dict[str, tuple[float, WalletStatus]] = {}


def _key(exchange: str, symbol: str) -> str:
    return f"{exchange}:{symbol.upper()}"


def wallet_cache_get(exchange: str, symbol: str) -> Optional[WalletStatus]:
    item = _store.get(_key(exchange, symbol))
    if not item:
        return None
    if time.time() - item[0] > TTL:
        return None
    return item[1]


def wallet_cache_set(exchange: str, symbol: str, status: WalletStatus) -> None:
    _store[_key(exchange, symbol)] = (time.time(), status)

# ========================================================================
# POOL
# ========================================================================





logger = logging.getLogger(__name__)

# Короткий таймаут: если биржа не ответила — идём дальше
REQUEST_TIMEOUT_MS = 12_000

_instances: dict[str, ccxt.Exchange] = {}
_lock = asyncio.Lock()
_fetch_locks: dict[str, asyncio.Lock] = {}


def _cache_key(class_name: str, extra: Optional[dict]) -> str:
    if not extra:
        return class_name
    opts = extra.get("options") or {}
    return f"{class_name}:{opts.get('defaultType', 'default')}"


def _build(class_name: str, extra: Optional[dict] = None) -> ccxt.Exchange:
    params: dict[str, Any] = {
        "enableRateLimit": True,
        "timeout": REQUEST_TIMEOUT_MS,
    }
    params.update(credentials_for(class_name))
    if extra:
        params.update(extra)
    exchange_class: Callable[..., ccxt.Exchange] = getattr(ccxt, class_name)
    return exchange_class(params)


async def get_exchange(class_name: str, extra: Optional[dict] = None) -> ccxt.Exchange:
    key = _cache_key(class_name, extra)
    if key in _instances:
        return _instances[key]

    async with _lock:
        if key in _instances:
            return _instances[key]
        exchange = _build(class_name, extra)
        _instances[key] = exchange
        return exchange


def _fetch_lock(key: str) -> asyncio.Lock:
    if key not in _fetch_locks:
        _fetch_locks[key] = asyncio.Lock()
    return _fetch_locks[key]


async def fetch_ticker_safe(
    class_name: str,
    symbol: str,
    extra: Optional[dict] = None,
    timeout_sec: float = 12.0,
) -> Optional[dict]:
    """fetch_ticker с блокировкой — один запрос на инстанс биржи за раз."""
    ex = await get_exchange(class_name, extra)
    key = _cache_key(class_name, extra)
    async with _fetch_lock(key):
        return await asyncio.wait_for(ex.fetch_ticker(symbol), timeout=timeout_sec)


async def close_all() -> None:
    global _instances
    for ex in _instances.values():
        try:
            await ex.close()
        except Exception as exc:  # noqa: BLE001
            logger.debug("close %s: %s", ex.id, exc)
    _instances = {}
    _fetch_locks.clear()

# ========================================================================
# FAST_PRICES
# ========================================================================




logger = logging.getLogger(__name__)

_SSL = ssl.create_default_context(cafile=certifi.where())
_SESSION: aiohttp.ClientSession | None = None
# Таймаут одного HTTP-запроса (сек)
HTTP_TIMEOUT = 0.85


async def _session() -> aiohttp.ClientSession:
    global _SESSION
    if _SESSION is None or _SESSION.closed:
        _SESSION = aiohttp.ClientSession(
            connector=aiohttp.TCPConnector(ssl=_SSL, limit=64),
            timeout=aiohttp.ClientTimeout(total=HTTP_TIMEOUT),
        )
    return _SESSION


async def close_fast_session() -> None:
    global _SESSION
    if _SESSION and not _SESSION.closed:
        await _SESSION.close()
    _SESSION = None


async def _get(url: str, params: Optional[dict] = None, json_body: Any = None) -> Any:
    try:
        session = await _session()
        if json_body is not None:
            async with session.post(url, json=json_body) as resp:
                if resp.status != 200:
                    return None
                return await resp.json()
        async with session.get(url, params=params or {}) as resp:
            if resp.status != 200:
                return None
            return await resp.json()
    except Exception as exc:  # noqa: BLE001
        logger.debug("fast %s: %s", url[:45], exc)
        return None


def _pair(base: str, quote: str) -> tuple[str, str, str]:
    b, q = base.upper(), quote.upper()
    return b, q, f"{b}{q}"


# --- Binance ---


async def fetch_binance(base: str, quote: str) -> tuple[Optional[float], Optional[float]]:
    _, _, sym = _pair(base, quote)
    spot_raw, fut_raw = await asyncio.gather(
        _get("https://api.binance.com/api/v3/ticker/price", {"symbol": sym}),
        _get("https://fapi.binance.com/fapi/v1/ticker/price", {"symbol": sym}),
    )
    spot = float(spot_raw["price"]) if isinstance(spot_raw, dict) and spot_raw.get("price") else None
    fut = float(fut_raw["price"]) if isinstance(fut_raw, dict) and fut_raw.get("price") else None
    return spot, fut


# --- Bybit ---


def _bybit_price(data: Any) -> Optional[float]:
    if isinstance(data, dict) and data.get("retCode") == 0:
        items = (data.get("result") or {}).get("list") or []
        if items and items[0].get("lastPrice"):
            return float(items[0]["lastPrice"])
    return None


async def fetch_bybit(base: str, quote: str) -> tuple[Optional[float], Optional[float]]:
    _, _, sym = _pair(base, quote)
    spot_raw, fut_raw = await asyncio.gather(
        _get("https://api.bybit.com/v5/market/tickers", {"category": "spot", "symbol": sym}),
        _get("https://api.bybit.com/v5/market/tickers", {"category": "linear", "symbol": sym}),
    )
    return _bybit_price(spot_raw), _bybit_price(fut_raw)


# --- Gate ---


async def fetch_gate(base: str, quote: str) -> tuple[Optional[float], Optional[float]]:
    b, q, _ = _pair(base, quote)
    pair = f"{b}_{q}"
    spot_raw, fut_raw = await asyncio.gather(
        _get("https://api.gateio.ws/api/v4/spot/tickers", {"currency_pair": pair}),
        _get("https://api.gateio.ws/api/v4/futures/usdt/tickers", {"contract": pair}),
    )
    spot = None
    if isinstance(spot_raw, list) and spot_raw:
        spot = float(spot_raw[0].get("last") or 0) or None
    fut = None
    if isinstance(fut_raw, list) and fut_raw:
        fut = float(fut_raw[0].get("last") or 0) or None
    return spot, fut


# --- MEXC ---


async def fetch_mexc(base: str, quote: str) -> tuple[Optional[float], Optional[float]]:
    b, q, sym = _pair(base, quote)
    spot_raw, fut_raw = await asyncio.gather(
        _get("https://api.mexc.com/api/v3/ticker/price", {"symbol": sym}),
        _get("https://contract.mexc.com/api/v1/contract/ticker", {"symbol": f"{b}_{q}"}),
    )
    spot = float(spot_raw["price"]) if isinstance(spot_raw, dict) and spot_raw.get("price") else None
    fut = None
    if isinstance(fut_raw, dict):
        d = fut_raw.get("data") or {}
        if d.get("lastPrice") is not None:
            fut = float(d["lastPrice"])
    return spot, fut


# --- OKX ---


async def fetch_okx(base: str, quote: str) -> tuple[Optional[float], Optional[float]]:
    b, q, _ = _pair(base, quote)
    inst = f"{b}-{q}"
    swap = f"{inst}-SWAP"
    spot_raw, fut_raw = await asyncio.gather(
        _get("https://www.okx.com/api/v5/market/ticker", {"instId": inst}),
        _get("https://www.okx.com/api/v5/market/ticker", {"instId": swap}),
    )
    def _px(data: Any) -> Optional[float]:
        if isinstance(data, dict) and data.get("code") == "0":
            rows = data.get("data") or []
            if rows and rows[0].get("last"):
                return float(rows[0]["last"])
        return None
    return _px(spot_raw), _px(fut_raw)


# --- Bitget ---


async def fetch_bitget(base: str, quote: str) -> tuple[Optional[float], Optional[float]]:
    b, q, sym = _pair(base, quote)
    spot_raw, fut_raw = await asyncio.gather(
        _get("https://api.bitget.com/api/v2/spot/market/tickers", {"symbol": sym}),
        _get(
            "https://api.bitget.com/api/v2/mix/market/ticker",
            {"symbol": sym, "productType": "USDT-FUTURES"},
        ),
    )
    def _spot(data: Any) -> Optional[float]:
        if isinstance(data, dict):
            rows = data.get("data") or []
            if rows and rows[0].get("lastPr"):
                return float(rows[0]["lastPr"])
        return None
    def _fut(data: Any) -> Optional[float]:
        if isinstance(data, dict):
            rows = data.get("data") or []
            if rows and rows[0].get("lastPr"):
                return float(rows[0]["lastPr"])
        return None
    return _spot(spot_raw), _fut(fut_raw)


# --- KuCoin ---


async def fetch_kucoin(base: str, quote: str) -> tuple[Optional[float], Optional[float]]:
    b, q, _ = _pair(base, quote)
    spot_sym = f"{b}-{q}"
    fut_sym = f"{b}{q}M"
    spot_raw, fut_raw = await asyncio.gather(
        _get("https://api.kucoin.com/api/v1/market/orderbook/level1", {"symbol": spot_sym}),
        _get("https://api-futures.kucoin.com/api/v1/ticker", {"symbol": fut_sym}),
    )
    spot = None
    if isinstance(spot_raw, dict) and spot_raw.get("code") == "200000":
        p = (spot_raw.get("data") or {}).get("price")
        if p:
            spot = float(p)
    fut = None
    if isinstance(fut_raw, dict) and fut_raw.get("code") == "200000":
        p = (fut_raw.get("data") or {}).get("price")
        if p:
            fut = float(p)
    return spot, fut


# --- BingX ---


async def fetch_bingx(base: str, quote: str) -> tuple[Optional[float], Optional[float]]:
    b, q, sym = _pair(base, quote)
    pair = f"{b}-{q}"
    spot_raw, fut_raw = await asyncio.gather(
        _get("https://open-api.bingx.com/openApi/spot/v1/ticker/price", {"symbol": pair}),
        _get("https://open-api.bingx.com/openApi/swap/v2/quote/price", {"symbol": pair}),
    )
    spot = None
    if isinstance(spot_raw, dict):
        d = spot_raw.get("data")
        if isinstance(d, dict) and d.get("price"):
            spot = float(d["price"])
        elif isinstance(d, list) and d and d[0].get("price"):
            spot = float(d[0]["price"])
    fut = None
    if isinstance(fut_raw, dict):
        d = fut_raw.get("data") or {}
        if d.get("price"):
            fut = float(d["price"])
    return spot, fut


# --- HTX ---


async def fetch_htx(base: str, quote: str) -> tuple[Optional[float], Optional[float]]:
    b, q, _ = _pair(base, quote)
    sym = f"{b.lower()}{q.lower()}"
    spot_raw, fut_raw = await asyncio.gather(
        _get("https://api.huobi.pro/market/detail/merged", {"symbol": sym}),
        _get(
            "https://api.hbdm.com/linear-swap-ex/market/detail/merged",
            {"contract_code": f"{b}-{q}"},
        ),
    )
    def _huobi(data: Any) -> Optional[float]:
        if isinstance(data, dict) and data.get("status") == "ok":
            tick = data.get("tick") or {}
            if tick.get("close"):
                return float(tick["close"])
        return None
    return _huobi(spot_raw), _huobi(fut_raw)


# --- Aster ---


async def fetch_aster(base: str, quote: str) -> tuple[Optional[float], Optional[float]]:
    _, _, sym = _pair(base, quote)
    fut_raw = await _get("https://fapi.asterdex.com/fapi/v1/ticker/price", {"symbol": sym})
    fut = float(fut_raw["price"]) if isinstance(fut_raw, dict) and fut_raw.get("price") else None
    return None, fut


# --- Hyperliquid ---


async def fetch_hyperliquid(base: str, quote: str) -> tuple[Optional[float], Optional[float]]:
    b, _, _ = _pair(base, quote)
    if quote.upper() == "USDT":
        # meta + mids в одном запросе тяжёлый — только mids
        mids = await _get(
            "https://api.hyperliquid.xyz/info",
            json_body={"type": "allMids"},
        )
        if isinstance(mids, dict) and b in mids:
            return None, float(mids[b])
    return None, None


FAST: dict[str, Callable[[str, str], Coroutine[Any, Any, tuple[Optional[float], Optional[float]]]]] = {
    "binance": fetch_binance,
    "bybit": fetch_bybit,
    "gate": fetch_gate,
    "mexc": fetch_mexc,
    "okx": fetch_okx,
    "bitget": fetch_bitget,
    "kucoin": fetch_kucoin,
    "bingx": fetch_bingx,
    "htx": fetch_htx,
    "aster": fetch_aster,
    "hyperliquid": fetch_hyperliquid,
}

# ========================================================================
# CONTRACTS
# ========================================================================





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


def contracts_cached(symbol: str) -> list[ContractInfo]:
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

# ========================================================================
# WALLET
# ========================================================================





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

# ========================================================================
# FORMATTER
# ========================================================================




def _fmt_price(value: float) -> str:
    if value >= 1000:
        s = f"{value:,.2f}"
    elif value >= 1:
        s = f"{value:.4f}"
    else:
        s = f"{value:.4f}"
    return f"${s.rstrip('0').rstrip('.')}"


def _icon(flag: Optional[bool]) -> str:
    if flag is True:
        return "✅"
    if flag is False:
        return "❌"
    return "❓"


def _contracts_block(contracts: list[ContractInfo]) -> list[str]:
    """Блок контрактов — всегда в сообщении."""
    lines = ["<b>Контракти:</b>"]
    if contracts:
        for c in contracts:
            addr = c.address.strip()
            lines.append(
                f"• {html.escape(c.network)}: <code>{html.escape(addr)}</code>"
            )
    else:
        lines.append(
            "<i>немає EVM-контракту (нативна монета або дуже новий токен)</i>"
        )
    lines.append("")
    return lines


def _network_lines(wallet: Optional[WalletStatus], futures_only: bool = False) -> list[str]:
    if futures_only or not wallet or not wallet.networks:
        return []
    lines: list[str] = []
    for net in wallet.networks:
        # Без данных не показываем ❓ — только реальные статусы
        if net.deposit is None and net.withdraw is None:
            continue
        d = _icon(net.deposit)
        w = _icon(net.withdraw)
        lines.append(f"• {html.escape(net.network)}: D {d} | W {w}")
    return lines


def _exchange_line(snap: ExchangeSnapshot) -> str:
    """• Bybit ($0.28) | Futures ($0.27)"""
    parts: list[str] = ["• "]

    if snap.futures_only and snap.futures:
        f = snap.futures
        parts.append(f"<b>{html.escape(snap.name)}</b>:")
        if f.url:
            parts.append(
                f' <a href="{html.escape(f.url)}">Futures</a> ({_fmt_price(f.price)})'
            )
        else:
            parts.append(f" Futures ({_fmt_price(f.price)})")
        return "".join(parts)

    if snap.spot:
        name = html.escape(snap.name)
        if snap.spot.url:
            parts.append(f'<a href="{html.escape(snap.spot.url)}"><b>{name}</b></a>')
        else:
            parts.append(f"<b>{name}</b>")
        parts.append(f" ({_fmt_price(snap.spot.price)})")
    elif snap.futures:
        parts.append(f"<b>{html.escape(snap.name)}</b>")
    else:
        return ""

    if snap.wallet and snap.wallet.note:
        parts.append(f" <i>({html.escape(snap.wallet.note)})</i>")

    if snap.futures:
        f = snap.futures
        if f.url:
            parts.append(
                f' | <a href="{html.escape(f.url)}">Futures</a> ({_fmt_price(f.price)})'
            )
        else:
            parts.append(f" | Futures ({_fmt_price(f.price)})")

    return "".join(parts)


def format_report(
    base: str,
    quote: str,
    snapshots: Iterable[ExchangeSnapshot],
    contracts: Optional[list[ContractInfo]] = None,
) -> str:
    items = [s for s in snapshots if s.has_data]
    ticker = html.escape(base.upper())

    if not items:
        return (
            f"<b>{ticker}</b>\n\n"
            f"❌ Монета не знайдена на біржах.\n"
            f"Спробуй: <code>{html.escape(base)}{html.escape(quote)}</code>"
        )

    lines = [f"<b>{ticker}</b>", ""]
    lines.extend(_contracts_block(contracts or []))
    lines.append(f"<b>Біржі ({len(items)}):</b>")
    lines.append("")

    for snap in items:
        header = _exchange_line(snap)
        if header:
            lines.append(header)
        lines.extend(_network_lines(snap.wallet, snap.futures_only))
        lines.append("")

    return "\n".join(lines).strip()

# ========================================================================
# FETCHER
# ========================================================================




logger = logging.getLogger(__name__)

# Лимит на быстрый /get (сек)
FETCH_BUDGET = 1.05
PRIORITY_KEYS = frozenset({"binance", "bybit"})

EXCHANGE_DEFS: list[dict[str, Any]] = [
    {"key": "binance", "name": "Binance", "futures_only": False},
    {"key": "bybit", "name": "Bybit", "futures_only": False},
    {"key": "gate", "name": "Gate.io", "futures_only": False},
    {"key": "mexc", "name": "MEXC", "futures_only": False},
    {"key": "bitget", "name": "Bitget", "futures_only": False},
    {"key": "okx", "name": "OKX", "futures_only": False},
    {"key": "kucoin", "name": "KuCoin", "futures_only": False},
    {"key": "bingx", "name": "BingX", "futures_only": False},
    {"key": "htx", "name": "HTX", "futures_only": False},
    {"key": "aster", "name": "AsterDex", "futures_only": True},
    {"key": "hyperliquid", "name": "Hyperliquid", "futures_only": True},
]


async def _fetch_one_prices(defn: dict[str, Any], base: str, quote: str) -> ExchangeSnapshot:
    key = defn["key"]
    snap = ExchangeSnapshot(
        key=key,
        name=defn["name"],
        futures_only=bool(defn.get("futures_only")),
    )
    fetcher = FAST.get(key)
    if not fetcher:
        return snap
    try:
        spot_p, fut_p = await fetcher(base, quote)
    except Exception as exc:  # noqa: BLE001
        logger.debug("fast price %s: %s", key, exc)
        return snap

    if spot_p is not None:
        snap.spot = MarketTicker(spot_p, spot_url(key, base, quote))
    if fut_p is not None:
        snap.futures = MarketTicker(fut_p, futures_url(key, base, quote))
    return snap


def _merge_snapshots(by_key: dict[str, ExchangeSnapshot]) -> list[ExchangeSnapshot]:
    return [by_key[d["key"]] for d in EXCHANGE_DEFS if d["key"] in by_key]


async def _gather_prices(base: str, quote: str, budget: float) -> list[ExchangeSnapshot]:
    by_key: dict[str, ExchangeSnapshot] = {}
    loop = asyncio.get_running_loop()
    deadline = loop.time() + budget
    prio = [d for d in EXCHANGE_DEFS if d["key"] in PRIORITY_KEYS]
    rest = [d for d in EXCHANGE_DEFS if d["key"] not in PRIORITY_KEYS]

    for item in await asyncio.gather(
        *[_fetch_one_prices(d, base, quote) for d in prio],
        return_exceptions=True,
    ):
        if isinstance(item, ExchangeSnapshot) and item.has_data:
            by_key[item.key] = item

    remaining = deadline - loop.time()
    if remaining > 0.05 and rest:
        tasks = [asyncio.create_task(_fetch_one_prices(d, base, quote)) for d in rest]
        done, pending = await asyncio.wait(tasks, timeout=remaining)
        for task in done:
            try:
                item = task.result()
            except Exception:
                continue
            if isinstance(item, ExchangeSnapshot) and item.has_data:
                by_key[item.key] = item
        for task in pending:
            task.cancel()
    return _merge_snapshots(by_key)


def _attach_wallets(
    snapshots: list[ExchangeSnapshot],
    base: str,
    fresh_wallets: Optional[dict[str, Any]] = None,
) -> None:
    for snap in snapshots:
        if snap.futures_only:
            continue
        w = None
        if fresh_wallets:
            w = fresh_wallets.get(snap.key)
        if w is None:
            w = wallet_cache_get(snap.key, base)
        snap.wallet = w


async def fetch_all_fast(
    base: str,
    quote: str,
) -> tuple[list[ExchangeSnapshot], list[ContractInfo]]:
    """
    Укладывается в ~1 с: только REST-цены + кэш контрактов/D/W.
    """
    contracts = contracts_cached(base)
    snapshots = await _gather_prices(base, quote, FETCH_BUDGET)
    _attach_wallets(snapshots, base)
    return snapshots, contracts


async def fetch_all_full(
    base: str,
    quote: str,
    snapshots: list[ExchangeSnapshot],
    contracts: list[ContractInfo],
) -> tuple[list[ExchangeSnapshot], list[ContractInfo]]:
    """Фон: все цены + контракты/D/W."""
    prev = {s.key: s for s in snapshots}
    price_raw = await asyncio.gather(
        *[_fetch_one_prices(d, base, quote) for d in EXCHANGE_DEFS],
        return_exceptions=True,
    )
    by_key = dict(prev)
    for item in price_raw:
        if isinstance(item, ExchangeSnapshot) and item.has_data:
            by_key[item.key] = item
    snapshots = _merge_snapshots(by_key)

    spot_defns = [d for d in EXCHANGE_DEFS if not d.get("futures_only")]

    full_contracts, wallets_raw = await asyncio.gather(
        fetch_contracts(base, listed_on=None),
        asyncio.gather(
            *[fetch_wallet(d["key"], base, None) for d in spot_defns],
            return_exceptions=True,
        ),
    )
    if not isinstance(full_contracts, list) or not full_contracts:
        full_contracts = contracts

    contract_networks = [c.network for c in full_contracts]

    fresh: dict[str, Any] = {}
    for defn, w in zip(spot_defns, wallets_raw):
        if isinstance(w, Exception):
            logger.debug("wallet %s %s: %s", defn["key"], base, w)
            continue
        w = enrich_wallet(w, base, contract_networks)
        fresh[defn["key"]] = w
        wallet_cache_set(defn["key"], base, w)

    _attach_wallets(snapshots, base, fresh)
    return snapshots, full_contracts


# Совместимость
async def fetch_all(base: str, quote: str) -> tuple[list[ExchangeSnapshot], list]:
    return await fetch_all_fast(base, quote)

# ========================================================================
# BOT
# ========================================================================




logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger(__name__)

router = Router()

_bg_tasks: set[asyncio.Task] = set()
_refreshing: set[str] = set()
_pending_edits: dict[str, list[Message]] = {}


def _pair_key(base: str, quote: str) -> str:
    return f"{base.upper()}:{quote.upper()}"


async def _edit_report(messages: list[Message], text: str) -> None:
    for msg in messages:
        try:
            await msg.edit_text(
                text,
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=True,
            )
        except TelegramBadRequest as exc:
            if "message is not modified" not in str(exc).lower():
                logger.debug("edit: %s", exc)


def _schedule_refresh(
    base: str,
    quote: str,
    snapshots,
    contracts,
    message: Message,
) -> None:
    key = _pair_key(base, quote)
    _pending_edits.setdefault(key, []).append(message)
    if key in _refreshing:
        return
    _refreshing.add(key)

    async def _job() -> None:
        try:
            snaps, cont = await fetch_all_full(base, quote, snapshots, contracts)
            text = format_report(base, quote, snaps, cont)
            report_cache_set(base, quote, text, snaps, cont, complete=True)
            targets = _pending_edits.pop(key, [])
            await _edit_report(targets, text)
            logger.info("full refresh %s (%d msg)", base, len(targets))
        except Exception:
            logger.exception("background refresh %s", base)
        finally:
            _refreshing.discard(key)
            _pending_edits.pop(key, None)

    task = asyncio.create_task(_job())
    _bg_tasks.add(task)
    task.add_done_callback(_bg_tasks.discard)


@router.message(Command("start", "help"))
async def cmd_start(message: Message) -> None:
    await message.answer(
        "👋 <b>Crypto Price Bot</b>\n\n"
        "<code>/get stx</code> — цены ~1 сек, контракти та D/W догружаються в повідомлення\n\n"
        "Повторний запит (25 сек) — миттєво з повними даними.",
        parse_mode=ParseMode.HTML,
    )


@router.message(Command("get"))
async def cmd_get(message: Message, command: CommandObject) -> None:
    if not command.args:
        await message.answer("Укажи тикер: <code>/get stx</code>", parse_mode=ParseMode.HTML)
        return

    raw = command.args.strip().split()[0]
    try:
        base, quote = parse_ticker(raw)
    except ValueError as exc:
        await message.answer(f"❌ {html.escape(str(exc))}", parse_mode=ParseMode.HTML)
        return

    t0 = time.perf_counter()

    cached = report_cache_get(base, quote)
    if cached and cached.complete:
        await message.answer(
            cached.text,
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
        )
        logger.info("cache hit %s %.0fms", base, (time.perf_counter() - t0) * 1000)
        return

    status = await message.answer(
        f"⏳ <b>{html.escape(base)}</b>…",
        parse_mode=ParseMode.HTML,
    )

    try:
        snapshots, contracts = await fetch_all_fast(base, quote)
        report = format_report(base, quote, snapshots, contracts)
        report_cache_set(base, quote, report, snapshots, contracts, complete=False)

        await status.edit_text(
            report,
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
        )
        elapsed = (time.perf_counter() - t0) * 1000
        logger.info("fast %s %.0fms", base, elapsed)

        _schedule_refresh(base, quote, snapshots, contracts, status)

    except Exception:
        logger.exception("get %s", base)
        await status.edit_text("❌ Ошибка. Попробуй ещё раз.", parse_mode=ParseMode.HTML)


async def on_startup() -> None:
    async def _warm() -> None:
        await preload_binance_coins()
        await warmup_cache()

    asyncio.create_task(_warm())
    logger.info("Бот готов")


async def on_shutdown() -> None:
    for t in list(_bg_tasks):
        t.cancel()
    await close_wallet_session()
    await close_fast_session()
    await close_all()


async def main() -> None:
    token = require_token()
    bot = Bot(token=token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher()
    dp.include_router(router)
    dp.startup.register(on_startup)
    dp.shutdown.register(on_shutdown)

    logger.info("Бот запущен")
    await dp.start_polling(bot)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        sys.exit(0)

