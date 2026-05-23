"""
Цены через публичный REST — быстрее CCXT, без блокировок.
"""

from __future__ import annotations

import asyncio
import logging
import ssl
from typing import Any, Callable, Coroutine, Optional

import aiohttp
import certifi

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
