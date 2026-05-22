"""Быстрая загрузка цен: пул CCXT + параллельные запросы."""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Optional

import ccxt.async_support as ccxt

from links import futures_url, spot_url
from models import ExchangeSnapshot, MarketTicker
from pool import REQUEST_TIMEOUT_MS, get_exchange
from ticker_parser import to_ccxt_spot_symbol, to_ccxt_swap_symbol
from contracts import fetch_contracts
from wallet import fetch_wallet

logger = logging.getLogger(__name__)

# Порядок как на референс-скриншоте
EXCHANGE_DEFS: list[dict[str, Any]] = [
    {"key": "binance", "name": "Binance", "spot_class": "binance", "futures_class": "binanceusdm"},
    {
        "key": "bybit",
        "name": "Bybit",
        "spot_class": "bybit",
        "futures_class": "bybit",
        "futures_options": {"options": {"defaultType": "linear"}},
    },
    {
        "key": "gate",
        "name": "Gate.io",
        "spot_class": "gate",
        "futures_class": "gate",
        "futures_options": {"options": {"defaultType": "swap"}},
    },
    {
        "key": "mexc",
        "name": "MEXC",
        "spot_class": "mexc",
        "futures_class": "mexc",
        "futures_options": {"options": {"defaultType": "swap"}},
    },
    {
        "key": "bitget",
        "name": "Bitget",
        "spot_class": "bitget",
        "futures_class": "bitget",
        "futures_options": {"options": {"defaultType": "swap"}},
    },
    {
        "key": "okx",
        "name": "OKX",
        "spot_class": "okx",
        "futures_class": "okx",
        "spot_options": {"options": {"defaultType": "spot"}},
        "futures_options": {"options": {"defaultType": "swap"}},
    },
    {"key": "kucoin", "name": "KuCoin", "spot_class": "kucoin", "futures_class": "kucoinfutures"},
    {
        "key": "bingx",
        "name": "BingX",
        "spot_class": "bingx",
        "futures_class": "bingx",
        "futures_options": {"options": {"defaultType": "swap"}},
    },
    {
        "key": "htx",
        "name": "HTX",
        "spot_class": "htx",
        "futures_class": "htx",
        "futures_options": {"options": {"defaultType": "swap"}},
    },
    {"key": "aster", "name": "AsterDex", "futures_class": "aster", "futures_only": True},
    {"key": "hyperliquid", "name": "Hyperliquid", "futures_class": "hyperliquid", "futures_only": True},
]

_TICKER_TIMEOUT = REQUEST_TIMEOUT_MS / 1000 + 1


def _swap_symbol(key: str, base: str, quote: str) -> str:
    if key == "hyperliquid" and quote.upper() == "USDT":
        return f"{base}/USDC:USDC"
    return to_ccxt_swap_symbol(base, quote)


async def _fetch_ticker(
    class_name: str,
    symbol: str,
    extra: Optional[dict],
) -> Optional[float]:
    try:
        ex = await get_exchange(class_name, extra)
        raw = await asyncio.wait_for(ex.fetch_ticker(symbol), timeout=_TICKER_TIMEOUT)
        price = raw.get("last") or raw.get("close")
        return float(price) if price is not None else None
    except (asyncio.TimeoutError, ccxt.BadSymbol, ccxt.NetworkError, ccxt.ExchangeError):
        return None
    except Exception as exc:  # noqa: BLE001
        logger.debug("%s %s: %s", class_name, symbol, exc)
        return None


async def _fetch_prices(defn: dict[str, Any], base: str, quote: str) -> ExchangeSnapshot:
    """Только цены — максимально быстро."""
    key = defn["key"]
    spot_sym = to_ccxt_spot_symbol(base, quote)
    swap_sym = _swap_symbol(key, base, quote)

    coros: list[Any] = []
    kinds: list[str] = []

    if defn.get("spot_class"):
        coros.append(
            _fetch_ticker(defn["spot_class"], spot_sym, defn.get("spot_options"))
        )
        kinds.append("spot")
    if defn.get("futures_class"):
        coros.append(
            _fetch_ticker(defn["futures_class"], swap_sym, defn.get("futures_options"))
        )
        kinds.append("futures")

    results = await asyncio.gather(*coros, return_exceptions=True)

    def _val(kind: str) -> Any:
        if kind not in kinds:
            return None
        r = results[kinds.index(kind)]
        return None if isinstance(r, BaseException) else r

    snap = ExchangeSnapshot(
        key=key,
        name=defn["name"],
        futures_only=bool(defn.get("futures_only")),
    )

    spot_price = _val("spot")
    fut_price = _val("futures")

    if spot_price is not None:
        snap.spot = MarketTicker(spot_price, spot_url(key, base, quote))
    if fut_price is not None:
        snap.futures = MarketTicker(fut_price, futures_url(key, base, quote))

    return snap


async def fetch_all(
    base: str, quote: str
) -> tuple[list[ExchangeSnapshot], list]:
    """Цены → контракты с бирж → D/W по тем же сетям."""
    prices_raw = await asyncio.gather(
        *[_fetch_prices(d, base, quote) for d in EXCHANGE_DEFS],
        return_exceptions=True,
    )

    out: list[ExchangeSnapshot] = []
    for item in prices_raw:
        if isinstance(item, ExchangeSnapshot) and item.has_data:
            out.append(item)

    listed = [s.key for s in out]
    contracts = await fetch_contracts(base, listed_on=listed)
    contract_networks = [c.network for c in contracts]

    wallets_raw = await asyncio.gather(
        *[
            fetch_wallet(d["key"], base, contract_networks)
            for d in EXCHANGE_DEFS
        ],
        return_exceptions=True,
    )

    wallets: dict[str, Any] = {}
    for defn, w in zip(EXCHANGE_DEFS, wallets_raw):
        if not isinstance(w, BaseException):
            wallets[defn["key"]] = w

    for snap in out:
        snap.wallet = wallets.get(snap.key)

    return out, contracts
