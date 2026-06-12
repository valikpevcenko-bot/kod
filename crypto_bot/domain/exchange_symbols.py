"""Resolve user ticker (MIRA) to exchange-specific API symbols."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from typing import Any, Literal

from crypto_bot.core import guards
from crypto_bot.core.http import HttpClient
from crypto_bot.domain.links import kucoin_futures_symbol
from crypto_bot.domain.ticker import trading_pair

MarketKind = Literal["spot", "futures"]
IndexStore = dict[str, dict[str, str]]
_CACHE_TTL = 300


def pair_key(base: str, quote: str) -> str:
    return f"{base.upper()}:{quote.upper()}"


def _fallback_symbol(exchange: str, market: MarketKind, base: str, quote: str) -> str:
    b, q, concat = trading_pair(base, quote)
    if exchange == "gate":
        return f"{b}_{q}"
    if exchange == "okx":
        return f"{b}-{q}-SWAP" if market == "futures" else f"{b}-{q}"
    if exchange == "bingx":
        return f"{b}-{q}"
    if exchange == "kucoin":
        return kucoin_futures_symbol(b, q) if market == "futures" else f"{b}-{q}"
    if exchange == "mexc" and market == "futures":
        return f"{b}_{q}"
    if exchange == "kraken":
        asset = "XBT" if b == "BTC" else b
        if market == "futures":
            return f"PF_{asset}USD"
        return f"{asset}{q}"
    if exchange == "hyperliquid":
        return b
    return concat


class ExchangeSymbolResolver:
    """Maps BASE:QUOTE to real REST symbols per exchange and market."""

    def __init__(self, http: HttpClient) -> None:
        self._http = http
        self._indexes: dict[str, tuple[float, IndexStore]] = {}

    @staticmethod
    def _register(
        store: IndexStore,
        base: str,
        quote: str,
        market: MarketKind,
        api_symbol: str,
    ) -> None:
        b = str(base or "").strip().upper()
        q = str(quote or "").strip().upper()
        sym = str(api_symbol or "").strip()
        if not b or not q or not sym:
            return
        store.setdefault(pair_key(b, q), {})[market] = sym

    @staticmethod
    def _register_dash_display(
        store: IndexStore,
        display: str,
        market: MarketKind,
        api_symbol: str,
    ) -> None:
        text = str(display or "").strip().upper()
        if "-" not in text:
            return
        base, quote = text.split("-", 1)
        ExchangeSymbolResolver._register(store, base, quote, market, api_symbol)

    async def ensure(self, exchange: str) -> IndexStore:
        hit = self._indexes.get(exchange)
        if hit and time.time() - hit[0] < _CACHE_TTL:
            return hit[1]
        loader = _LOADERS.get(exchange)
        store: IndexStore = await loader(self) if loader else {}
        self._indexes[exchange] = (time.time(), store)
        return store

    async def get(
        self,
        exchange: str,
        base: str,
        quote: str,
        market: MarketKind,
        *,
        fast: bool = False,
    ) -> str:
        b, q, _ = trading_pair(base, quote)
        key = pair_key(b, q)
        if fast:
            return _fallback_symbol(exchange, market, b, q)
        cached = self._indexes.get(exchange)
        if cached and time.time() - cached[0] < _CACHE_TTL:
            hit = cached[1].get(key, {}).get(market)
            if hit:
                return hit
        lazy = _LAZY.get(exchange)
        if lazy:
            try:
                sym = await lazy(self, b, q, market)
            except Exception:
                sym = None
            if sym:
                if cached:
                    cached[1].setdefault(key, {})[market] = sym
                return sym
        return _fallback_symbol(exchange, market, b, q)

    async def preload(self, exchanges: list[str] | None = None) -> None:
        keys = exchanges or list(_LOADERS.keys())
        await asyncio.gather(
            *[self.ensure(ex) for ex in keys if ex in _LOADERS],
            return_exceptions=True,
        )


async def _load_binance(resolver: ExchangeSymbolResolver) -> IndexStore:
    store: IndexStore = {}
    for url, market in (
        ("https://api.binance.com/api/v3/exchangeInfo", "spot"),
        ("https://fapi.binance.com/fapi/v1/exchangeInfo", "futures"),
    ):
        data = await resolver._http.get_json(url, timeout=8)
        if not isinstance(data, dict):
            continue
        for item in data.get("symbols") or []:
            if not isinstance(item, dict) or item.get("status") != "TRADING":
                continue
            resolver._register(
                store,
                str(item.get("baseAsset") or ""),
                str(item.get("quoteAsset") or ""),
                market,
                str(item.get("symbol") or ""),
            )
    return store


async def _load_bybit(resolver: ExchangeSymbolResolver) -> IndexStore:
    store: IndexStore = {}
    for category, market in (("spot", "spot"), ("linear", "futures")):
        data = await resolver._http.get_json(
            "https://api.bybit.com/v5/market/instruments-info",
            params={"category": category, "limit": 1000},
            timeout=8,
        )
        if not isinstance(data, dict) or not guards.ret_ok(data.get("retCode")):
            continue
        for item in (data.get("result") or {}).get("list") or []:
            if not isinstance(item, dict) or item.get("status") != "Trading":
                continue
            resolver._register(
                store,
                str(item.get("baseCoin") or ""),
                str(item.get("quoteCoin") or ""),
                market,
                str(item.get("symbol") or ""),
            )
    return store


async def _load_gate(_resolver: ExchangeSymbolResolver) -> IndexStore:
    """Gate has huge pair lists — resolved lazily per ticker."""
    return {}


async def _lazy_gate(
    resolver: ExchangeSymbolResolver,
    base: str,
    quote: str,
    market: MarketKind,
) -> str | None:
    pair = f"{base.upper()}_{quote.upper()}"
    if market == "spot":
        data = await resolver._http.get_json(
            f"https://api.gateio.ws/api/v4/spot/currency_pairs/{pair}",
            timeout=6,
        )
        if isinstance(data, dict) and data.get("trade_status") == "tradable":
            return pair
        return None
    data = await resolver._http.get_json(
        f"https://api.gateio.ws/api/v4/futures/usdt/contracts/{pair}",
        timeout=6,
    )
    if isinstance(data, dict) and str(data.get("status") or "").lower() in ("active", "trading"):
        return pair
    return None


async def _load_mexc(resolver: ExchangeSymbolResolver) -> IndexStore:
    store: IndexStore = {}
    spot = await resolver._http.get_json(
        "https://api.mexc.com/api/v3/exchangeInfo",
        timeout=8,
    )
    if isinstance(spot, dict):
        for item in spot.get("symbols") or []:
            if not isinstance(item, dict) or item.get("status") != "ENABLED":
                continue
            resolver._register(
                store,
                str(item.get("baseAsset") or ""),
                str(item.get("quoteAsset") or ""),
                "spot",
                str(item.get("symbol") or ""),
            )
    fut = await resolver._http.get_json(
        "https://contract.mexc.com/api/v1/contract/detail",
        timeout=8,
    )
    if isinstance(fut, dict) and fut.get("success"):
        for item in fut.get("data") or []:
            if not isinstance(item, dict):
                continue
            sym = str(item.get("symbol") or "")
            resolver._register(
                store,
                str(item.get("baseCoin") or ""),
                str(item.get("quoteCoin") or ""),
                "futures",
                sym,
            )
            short = str(item.get("baseCoinName") or item.get("baseCoin") or "")
            if short:
                resolver._register(store, short, str(item.get("quoteCoin") or "USDT"), "futures", sym)
    return store


async def _load_okx(resolver: ExchangeSymbolResolver) -> IndexStore:
    store: IndexStore = {}
    for inst_type, market in (("SPOT", "spot"), ("SWAP", "futures")):
        data = await resolver._http.get_json(
            "https://www.okx.com/api/v5/public/instruments",
            params={"instType": inst_type},
            timeout=8,
        )
        if not isinstance(data, dict) or not guards.okx_ok(data.get("code")):
            continue
        for item in data.get("data") or []:
            if not isinstance(item, dict) or item.get("state") != "live":
                continue
            resolver._register(
                store,
                str(item.get("baseCcy") or ""),
                str(item.get("quoteCcy") or ""),
                market,
                str(item.get("instId") or ""),
            )
    return store


async def _load_bitget(resolver: ExchangeSymbolResolver) -> IndexStore:
    store: IndexStore = {}
    spot = await resolver._http.get_json(
        "https://api.bitget.com/api/v2/spot/public/symbols",
        timeout=8,
    )
    if isinstance(spot, dict):
        for item in spot.get("data") or []:
            if not isinstance(item, dict) or item.get("status") != "online":
                continue
            resolver._register(
                store,
                str(item.get("baseCoin") or ""),
                str(item.get("quoteCoin") or ""),
                "spot",
                str(item.get("symbol") or ""),
            )
    fut = await resolver._http.get_json(
        "https://api.bitget.com/api/v2/mix/market/contracts",
        params={"productType": "USDT-FUTURES"},
        timeout=8,
    )
    if isinstance(fut, dict):
        for item in fut.get("data") or []:
            if not isinstance(item, dict):
                continue
            resolver._register(
                store,
                str(item.get("baseCoin") or ""),
                str(item.get("quoteCoin") or ""),
                "futures",
                str(item.get("symbol") or ""),
            )
    return store


async def _load_kucoin(resolver: ExchangeSymbolResolver) -> IndexStore:
    store: IndexStore = {}
    spot = await resolver._http.get_json(
        "https://api.kucoin.com/api/v2/symbols",
        timeout=8,
    )
    if isinstance(spot, dict) and guards.kucoin_ok(spot.get("code")):
        for item in spot.get("data") or []:
            if not isinstance(item, dict) or not item.get("enableTrading"):
                continue
            resolver._register(
                store,
                str(item.get("baseCurrency") or ""),
                str(item.get("quoteCurrency") or ""),
                "spot",
                str(item.get("symbol") or ""),
            )
    fut = await resolver._http.get_json(
        "https://api-futures.kucoin.com/api/v1/contracts/active",
        timeout=8,
    )
    if isinstance(fut, dict) and guards.kucoin_ok(fut.get("code")):
        for item in fut.get("data") or []:
            if not isinstance(item, dict):
                continue
            resolver._register(
                store,
                str(item.get("baseCurrency") or ""),
                str(item.get("quoteCurrency") or ""),
                "futures",
                str(item.get("symbol") or ""),
            )
    return store


async def _load_bingx(resolver: ExchangeSymbolResolver) -> IndexStore:
    store: IndexStore = {}
    spot_raw = await resolver._http.get_json(
        "https://open-api.bingx.com/openApi/spot/v1/common/symbols",
        timeout=8,
    )
    if isinstance(spot_raw, dict) and guards.code_ok(spot_raw.get("code"), 0, "0"):
        for item in (spot_raw.get("data") or {}).get("symbols") or []:
            if not isinstance(item, dict) or item.get("status") != 1:
                continue
            api_sym = str(item.get("symbol") or "")
            resolver._register_dash_display(
                store,
                str(item.get("displayName") or api_sym),
                "spot",
                api_sym,
            )
            asset = str(item.get("asset") or item.get("coin") or "")
            if asset:
                resolver._register(store, asset, "USDT", "spot", api_sym)
    swap_raw = await resolver._http.get_json(
        "https://open-api.bingx.com/openApi/swap/v2/quote/contracts",
        timeout=8,
    )
    if isinstance(swap_raw, dict) and guards.code_ok(swap_raw.get("code"), 0, "0"):
        for item in swap_raw.get("data") or []:
            if not isinstance(item, dict) or item.get("status") != 1:
                continue
            api_sym = str(item.get("symbol") or "")
            resolver._register_dash_display(
                store,
                str(item.get("displayName") or ""),
                "futures",
                api_sym,
            )
            asset = str(item.get("asset") or "")
            if asset:
                resolver._register(store, asset, "USDT", "futures", api_sym)
    return store


def _kraken_base_name(wsname: str | None, altname: str) -> str | None:
    if wsname and "/" in wsname:
        base = wsname.split("/", 1)[0].strip().upper()
    else:
        base = altname.upper()
        for q in ("USDT", "USDC", "USD", "EUR"):
            if base.endswith(q):
                base = base[: -len(q)]
                break
    if base == "XBT":
        return "BTC"
    return base or None


def _kraken_quote_name(quote_code: str) -> str:
    q = quote_code.upper()
    if q in ("ZUSD", "USD"):
        return "USD"
    return q


async def _load_kraken(resolver: ExchangeSymbolResolver) -> IndexStore:
    store: IndexStore = {}
    spot_raw = await resolver._http.get_json(
        "https://api.kraken.com/0/public/AssetPairs",
        timeout=8,
    )
    if isinstance(spot_raw, dict) and not spot_raw.get("error"):
        for item in (spot_raw.get("result") or {}).values():
            if not isinstance(item, dict) or item.get("status") != "online":
                continue
            altname = str(item.get("altname") or "")
            base = _kraken_base_name(item.get("wsname"), altname)
            quote = _kraken_quote_name(str(item.get("quote") or ""))
            if not base or not quote:
                continue
            resolver._register(store, base, quote, "spot", altname)

    fut_raw = await resolver._http.get_json(
        "https://futures.kraken.com/derivatives/api/v3/tickers",
        timeout=8,
    )
    if isinstance(fut_raw, dict) and fut_raw.get("result") == "success":
        for row in fut_raw.get("tickers") or []:
            if not isinstance(row, dict) or row.get("tag") != "perpetual":
                continue
            sym = str(row.get("symbol") or "")
            if not sym.startswith("PF_"):
                continue
            pair = str(row.get("pair") or "")
            if ":" not in pair:
                continue
            base = pair.split(":", 1)[0].strip().upper()
            if base == "XBT":
                base = "BTC"
            resolver._register(store, base, "USDT", "futures", sym)
            resolver._register(store, base, "USD", "futures", sym)
    return store


async def _load_aster(resolver: ExchangeSymbolResolver) -> IndexStore:
    store: IndexStore = {}
    for url, market in (
        ("https://sapi.asterdex.com/api/v3/exchangeInfo", "spot"),
        ("https://fapi.asterdex.com/fapi/v1/exchangeInfo", "futures"),
    ):
        data = await resolver._http.get_json(url, timeout=8)
        if not isinstance(data, dict):
            continue
        for item in data.get("symbols") or []:
            if not isinstance(item, dict) or item.get("status") != "TRADING":
                continue
            resolver._register(
                store,
                str(item.get("baseAsset") or ""),
                str(item.get("quoteAsset") or ""),
                market,
                str(item.get("symbol") or ""),
            )
    return store


async def _load_hyperliquid(resolver: ExchangeSymbolResolver) -> IndexStore:
    store: IndexStore = {}
    raw = await resolver._http.post_json(
        "https://api.hyperliquid.xyz/info",
        json_body={"type": "metaAndAssetCtxs"},
        timeout=8,
    )
    if not isinstance(raw, list) or len(raw) < 1:
        return store
    meta = raw[0] if isinstance(raw[0], dict) else {}
    for asset in meta.get("universe") or []:
        name = asset.get("name") if isinstance(asset, dict) else str(asset)
        if name:
            resolver._register(store, str(name), "USDT", "futures", str(name))
    return store


_LAZY: dict[
    str,
    Callable[[ExchangeSymbolResolver, str, str, MarketKind], Awaitable[str | None]],
] = {
    "gate": _lazy_gate,
}

_LOADERS: dict[str, Callable[[ExchangeSymbolResolver], Awaitable[IndexStore]]] = {
    "binance": _load_binance,
    "bybit": _load_bybit,
    "gate": _load_gate,
    "mexc": _load_mexc,
    "okx": _load_okx,
    "bitget": _load_bitget,
    "kucoin": _load_kucoin,
    "bingx": _load_bingx,
    "kraken": _load_kraken,
    "aster": _load_aster,
    "hyperliquid": _load_hyperliquid,
}
