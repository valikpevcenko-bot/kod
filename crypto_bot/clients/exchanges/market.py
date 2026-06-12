"""Exchange spot/futures prices and funding rates."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from typing import Any

import structlog

from crypto_bot.core import guards
from crypto_bot.core.http import get_http
from crypto_bot.core.price_fast import price_fast_ctx
from crypto_bot.domain.exchanges import FUNDING_CACHE_TTL, FUNDING_FETCH_TIMEOUT
from crypto_bot.domain.exchange_symbols import ExchangeSymbolResolver, MarketKind
from crypto_bot.domain.ticker import trading_pair
from crypto_bot.models.market import MarketTicker

logger = structlog.get_logger(__name__)

PriceFn = Callable[[str, str], Awaitable[tuple[float | None, float | None]]]
FundingFn = Callable[[str, str], Awaitable[tuple[float | None, int | None]]]

_funding_cache: dict[str, tuple[float, dict[str, tuple[float | None, int | None]]]] = {}
_mexc_fut_cache: tuple[float, dict[str, float]] | None = None
_MEXC_FUT_CACHE_TTL = 120
_hl_asset_cache: tuple[float, dict[str, dict[str, Any]]] | None = None
_HL_ASSET_CACHE_TTL = 25
_kraken_fut_cache: tuple[float, dict[str, dict[str, Any]]] | None = None
_KRAKEN_FUT_CACHE_TTL = 25
_LEG_TIMEOUT = 2.0
_LEG_RETRY_ATTEMPTS = 3


class ExchangeMarketClient:
    """Public market data from exchange REST APIs."""

    def __init__(self) -> None:
        self._http = get_http()
        self._resolver = ExchangeSymbolResolver(self._http)

    async def _sym(self, exchange: str, base: str, quote: str, market: MarketKind) -> str:
        return await self._resolver.get(
            exchange, base, quote, market, fast=price_fast_ctx.get()
        )

    async def spot_and_futures(
        self,
        exchange_key: str,
        base: str,
        quote: str,
        *,
        fast: bool = False,
    ) -> tuple[float | None, float | None]:
        fn = _PRICE_FETCHERS.get(exchange_key)
        if not fn:
            return None, None
        if not fast:
            return await fn(self, base, quote)
        token = price_fast_ctx.set(True)
        try:
            spot_fn = _SPOT_ONLY.get(exchange_key)
            fut_fn = _FUTURES_ONLY.get(exchange_key)
            leg_t = 0.48 if price_fast_ctx.get() else _LEG_TIMEOUT
            if spot_fn and fut_fn:
                spot_p, fut_p = await self._pair_resilient(
                    exchange_key,
                    lambda b, q: spot_fn(self, b, q),
                    lambda b, q: fut_fn(self, b, q),
                    base,
                    quote,
                    leg_timeout=leg_t,
                    leg_attempts=2,
                    skip_extra_retry=True,
                )
                if fut_p is not None and spot_p is None:
                    spot_p = await self._fetch_leg_resilient(
                        lambda b, q: spot_fn(self, b, q),
                        exchange_key,
                        base,
                        quote,
                        "spot",
                        attempts=2,
                        timeout=1.1,
                    )
                return spot_p, fut_p
            try:
                return await asyncio.wait_for(fn(self, base, quote), timeout=0.78)
            except asyncio.TimeoutError:
                return None, None
        finally:
            price_fast_ctx.reset(token)

    async def fetch_spot_only(self, exchange_key: str, base: str, quote: str) -> float | None:
        fn = _SPOT_ONLY.get(exchange_key)
        if not fn:
            return None
        return await self._fetch_leg_resilient(
            lambda b, q: fn(self, b, q),
            exchange_key,
            base,
            quote,
            "spot",
        )

    async def fetch_futures_only(self, exchange_key: str, base: str, quote: str) -> float | None:
        fn = _FUTURES_ONLY.get(exchange_key)
        if not fn:
            return None
        timeout = 4.5 if exchange_key == "mexc" else _LEG_TIMEOUT
        attempts = _LEG_RETRY_ATTEMPTS if exchange_key == "mexc" else 2
        return await self._fetch_leg_resilient(
            lambda b, q: fn(self, b, q),
            exchange_key,
            base,
            quote,
            "futures",
            attempts=attempts,
            timeout=timeout,
        )

    async def preload_resolvers(self, exchanges: list[str] | None = None) -> None:
        await self._resolver.preload(exchanges)

    async def warm_mexc_futures_map(self) -> None:
        try:
            await asyncio.wait_for(self._mexc_futures_ticker_map(), timeout=6.0)
        except Exception:
            pass

    async def warm_hyperliquid_assets(self) -> None:
        try:
            await asyncio.wait_for(self._hyperliquid_assets(), timeout=6.0)
        except Exception:
            pass

    async def warm_kraken_futures_tickers(self) -> None:
        try:
            await asyncio.wait_for(self._kraken_futures_tickers(), timeout=6.0)
        except Exception:
            pass

    async def _hyperliquid_assets(self) -> dict[str, dict[str, Any]]:
        global _hl_asset_cache
        now = time.time()
        if _hl_asset_cache and now - _hl_asset_cache[0] < _HL_ASSET_CACHE_TTL:
            return _hl_asset_cache[1]
        raw = await self._http.post_json(
            "https://api.hyperliquid.xyz/info",
            json_body={"type": "metaAndAssetCtxs"},
            timeout=4.0,
        )
        out: dict[str, dict[str, Any]] = {}
        if isinstance(raw, list) and len(raw) >= 2:
            meta, ctxs = raw[0], raw[1]
            universe = (meta or {}).get("universe") if isinstance(meta, dict) else []
            if isinstance(universe, list) and isinstance(ctxs, list):
                for idx, asset in enumerate(universe):
                    name = asset.get("name") if isinstance(asset, dict) else str(asset)
                    if not name or idx >= len(ctxs) or not isinstance(ctxs[idx], dict):
                        continue
                    out[str(name).upper()] = ctxs[idx]
        _hl_asset_cache = (now, out)
        return out

    async def _pair_resilient(
        self,
        exchange_key: str,
        spot_fn: Callable[[str, str], Awaitable[float | None]],
        fut_fn: Callable[[str, str], Awaitable[float | None]],
        base: str,
        quote: str,
        *,
        leg_timeout: float = _LEG_TIMEOUT,
        leg_attempts: int = 2,
        skip_extra_retry: bool = False,
    ) -> tuple[float | None, float | None]:
        """Spot + futures окремо з retry — якщо одна нога впала, добираємо другу."""
        spot_p, fut_p = await asyncio.gather(
            self._fetch_leg_resilient(
                spot_fn, exchange_key, base, quote, "spot",
                attempts=leg_attempts, timeout=leg_timeout,
            ),
            self._fetch_leg_resilient(
                fut_fn, exchange_key, base, quote, "futures",
                attempts=leg_attempts, timeout=leg_timeout,
            ),
        )
        if skip_extra_retry:
            return spot_p, fut_p
        if spot_p is not None and fut_p is None:
            fut_p = await self._fetch_leg_resilient(
                fut_fn, exchange_key, base, quote, "futures", attempts=_LEG_RETRY_ATTEMPTS
            )
        elif fut_p is not None and spot_p is None:
            spot_p = await self._fetch_leg_resilient(
                spot_fn, exchange_key, base, quote, "spot", attempts=_LEG_RETRY_ATTEMPTS
            )
        return spot_p, fut_p

    async def _fetch_leg_resilient(
        self,
        fn: Callable[[str, str], Awaitable[float | None]],
        exchange_key: str,
        base: str,
        quote: str,
        leg: str,
        *,
        attempts: int = 2,
        timeout: float = _LEG_TIMEOUT,
    ) -> float | None:
        last_exc: Exception | None = None
        for attempt in range(attempts):
            try:
                return await asyncio.wait_for(fn(base, quote), timeout=timeout)
            except Exception as exc:
                last_exc = exc
                if attempt < attempts - 1:
                    await asyncio.sleep(0.08 * (attempt + 1))
        if last_exc:
            logger.debug(
                "leg_fetch_fail",
                exchange=exchange_key,
                leg=leg,
                symbol=base,
                error=str(last_exc)[:80],
            )
        return None

    async def _mexc_futures_ticker_map(self) -> dict[str, float]:
        global _mexc_fut_cache
        now = time.time()
        if _mexc_fut_cache and now - _mexc_fut_cache[0] < _MEXC_FUT_CACHE_TTL:
            return _mexc_fut_cache[1]
        out: dict[str, float] = {}
        data = await self._http.get_json(
            "https://contract.mexc.com/api/v1/contract/ticker",
            timeout=8,
        )
        if isinstance(data, dict) and data.get("success"):
            rows = data.get("data") or []
            if isinstance(rows, dict):
                rows = [rows]
            for item in rows:
                if not isinstance(item, dict):
                    continue
                sym = str(item.get("symbol") or "").upper()
                price = guards.price_positive(item.get("lastPrice") or item.get("fairPrice"))
                if sym and price is not None:
                    out[sym] = price
        _mexc_fut_cache = (time.time(), out)
        return out

    @staticmethod
    def _parse_mexc_futures_payload(fut_raw: Any) -> float | None:
        if not isinstance(fut_raw, dict) or not fut_raw.get("success"):
            return None
        data = fut_raw.get("data")
        if isinstance(data, list) and data:
            data = data[0]
        if isinstance(data, dict):
            return guards.price_positive(data.get("lastPrice") or data.get("fairPrice"))
        return None

    async def funding(self, exchange_key: str, base: str, quote: str) -> tuple[float | None, int | None]:
        fn = _FUNDING_FETCHERS.get(exchange_key)
        if not fn:
            return None, None
        try:
            return await asyncio.wait_for(fn(self, base, quote), timeout=FUNDING_FETCH_TIMEOUT)
        except Exception as exc:
            logger.debug("funding_error", exchange=exchange_key, error=str(exc)[:80])
            return None, None

    async def funding_map(
        self,
        base: str,
        quote: str,
        keys: list[str] | None = None,
        *,
        timeout: float | None = None,
    ) -> dict[str, tuple[float | None, int | None]]:
        from crypto_bot.domain.exchanges import FUNDING_BATCH_TIMEOUT

        cache_key = f"{base.upper()}:{quote.upper()}"
        keys_list = keys or list(_FUNDING_FETCHERS.keys())
        merged: dict[str, tuple[float | None, int | None]] = {}
        hit = _funding_cache.get(cache_key)
        if hit and time.time() - hit[0] < FUNDING_CACHE_TTL:
            merged.update(hit[1])

        need = [
            k
            for k in keys_list
            if k in _FUNDING_FETCHERS
            and (merged.get(k) is None or merged[k][0] is None)
        ]
        if not need:
            return {k: merged[k] for k in keys_list if k in merged and merged[k][0] is not None}

        limit = timeout if timeout is not None else FUNDING_BATCH_TIMEOUT
        tasks = {k: asyncio.create_task(self.funding(k, base, quote)) for k in need}
        done, pending = await asyncio.wait(tasks.values(), timeout=limit)
        for key, task in tasks.items():
            if task not in done:
                task.cancel()
                continue
            try:
                result = task.result()
                if isinstance(result, tuple) and len(result) == 2 and result[0] is not None:
                    merged[key] = result
            except Exception:
                pass

        store = dict(_funding_cache.get(cache_key, (0, {}))[1]) if cache_key in _funding_cache else {}
        for k, v in merged.items():
            if v[0] is not None:
                store[k] = v
        _funding_cache[cache_key] = (time.time(), store)
        return {k: merged[k] for k in keys_list if k in merged and merged[k][0] is not None}

    # --- Binance ---

    async def binance_spot(self, base: str, quote: str) -> float | None:
        sym = await self._sym("binance", base, quote, "spot")
        spot_raw = await self._http.get_json(
            "https://api.binance.com/api/v3/ticker/price",
            params={"symbol": sym},
        )
        return guards.dict_field_price(spot_raw, "price")

    async def binance_futures(self, base: str, quote: str) -> float | None:
        sym = await self._sym("binance", base, quote, "futures")
        fut_raw = await self._http.get_json(
            "https://fapi.binance.com/fapi/v1/ticker/price",
            params={"symbol": sym},
        )
        return guards.dict_field_price(fut_raw, "price")

    async def binance_prices(self, base: str, quote: str) -> tuple[float | None, float | None]:
        return await self._pair_resilient("binance", self.binance_spot, self.binance_futures, base, quote)

    async def binance_funding(self, base: str, quote: str) -> tuple[float | None, int | None]:
        sym = await self._sym("binance", base, quote, "futures")
        data = await self._http.get_json(
            "https://fapi.binance.com/fapi/v1/premiumIndex",
            params={"symbol": sym},
        )
        if isinstance(data, dict) and data.get("lastFundingRate") is not None:
            return float(data["lastFundingRate"]), int(data.get("nextFundingTime") or 0)
        return None, None

    # --- Bybit ---

    def _bybit_price(self, data: Any) -> float | None:
        if isinstance(data, dict) and guards.ret_ok(data.get("retCode")):
            items = (data.get("result") or {}).get("list") or []
            if items and items[0].get("lastPrice"):
                return float(items[0]["lastPrice"])
        return None

    async def bybit_spot(self, base: str, quote: str) -> float | None:
        sym = await self._sym("bybit", base, quote, "spot")
        spot_raw = await self._http.get_json(
            "https://api.bybit.com/v5/market/tickers",
            params={"category": "spot", "symbol": sym},
        )
        return self._bybit_price(spot_raw)

    async def bybit_futures(self, base: str, quote: str) -> float | None:
        sym = await self._sym("bybit", base, quote, "futures")
        fut_raw = await self._http.get_json(
            "https://api.bybit.com/v5/market/tickers",
            params={"category": "linear", "symbol": sym},
        )
        return self._bybit_price(fut_raw)

    async def bybit_prices(self, base: str, quote: str) -> tuple[float | None, float | None]:
        return await self._pair_resilient("bybit", self.bybit_spot, self.bybit_futures, base, quote)

    async def bybit_funding(self, base: str, quote: str) -> tuple[float | None, int | None]:
        sym = await self._sym("bybit", base, quote, "futures")
        data = await self._http.get_json(
            "https://api.bybit.com/v5/market/tickers",
            params={"category": "linear", "symbol": sym},
        )
        if isinstance(data, dict) and guards.ret_ok(data.get("retCode")):
            items = (data.get("result") or {}).get("list") or []
            if items:
                row = items[0]
                rate = guards.float_field(row.get("fundingRate"))
                nxt = row.get("nextFundingTime")
                if rate is not None:
                    ts = None
                    if nxt is not None and str(nxt).strip():
                        try:
                            ts = int(nxt)
                        except (TypeError, ValueError):
                            ts = None
                    return rate, ts
        return None, None

    # --- Gate ---

    async def gate_spot(self, base: str, quote: str) -> float | None:
        pair = await self._sym("gate", base, quote, "spot")
        spot_raw = await self._http.get_json(
            "https://api.gateio.ws/api/v4/spot/tickers",
            params={"currency_pair": pair},
            timeout=5,
        )
        return guards.list_row_price(spot_raw, "last")

    async def gate_futures(self, base: str, quote: str) -> float | None:
        contract = await self._sym("gate", base, quote, "futures")
        fut_raw = await self._http.get_json(
            "https://api.gateio.ws/api/v4/futures/usdt/tickers",
            params={"contract": contract},
            timeout=5,
        )
        return guards.list_row_price(fut_raw, "last")

    async def gate_prices(self, base: str, quote: str) -> tuple[float | None, float | None]:
        return await self._pair_resilient("gate", self.gate_spot, self.gate_futures, base, quote)

    async def gate_funding(self, base: str, quote: str) -> tuple[float | None, int | None]:
        contract = await self._sym("gate", base, quote, "futures")
        data, fr_list = await asyncio.gather(
            self._http.get_json(f"https://api.gateio.ws/api/v4/futures/usdt/contracts/{contract}"),
            self._http.get_json(
                "https://api.gateio.ws/api/v4/futures/usdt/funding_rate",
                params={"contract": contract},
            ),
        )
        if isinstance(data, dict):
            rate = guards.float_field(
                data.get("funding_rate") or data.get("funding_rate_indicative")
            )
            nxt = data.get("funding_next_apply")
            if rate is not None:
                ts = int(nxt) * 1000 if nxt else None
                return rate, ts
        if isinstance(fr_list, list) and fr_list and isinstance(fr_list[0], dict):
            rate = guards.float_field(fr_list[0].get("r"))
            if rate is not None:
                return rate, None
        return None, None

    # --- MEXC ---

    async def mexc_spot(self, base: str, quote: str) -> float | None:
        sym = await self._sym("mexc", base, quote, "spot")
        spot_raw = await self._http.get_json(
            "https://api.mexc.com/api/v3/ticker/price",
            params={"symbol": sym},
        )
        return guards.dict_field_price(spot_raw, "price")

    async def mexc_futures(self, base: str, quote: str) -> float | None:
        contract_sym = await self._sym("mexc", base, quote, "futures")
        fut_raw = await self._http.get_json(
            "https://contract.mexc.com/api/v1/contract/ticker",
            params={"symbol": contract_sym},
            timeout=1.2 if price_fast_ctx.get() else 4.0,
        )
        price = self._parse_mexc_futures_payload(fut_raw)
        if price is not None:
            return price
        if price_fast_ctx.get():
            return None
        tickers = await self._mexc_futures_ticker_map()
        return tickers.get(contract_sym.upper())

    async def mexc_prices(self, base: str, quote: str) -> tuple[float | None, float | None]:
        return await self._pair_resilient("mexc", self.mexc_spot, self.mexc_futures, base, quote)

    async def mexc_funding(self, base: str, quote: str) -> tuple[float | None, int | None]:
        contract_sym = await self._sym("mexc", base, quote, "futures")
        data = await self._http.get_json(
            f"https://contract.mexc.com/api/v1/contract/funding_rate/{contract_sym}"
        )
        if isinstance(data, dict) and data.get("success"):
            payload = data.get("data") or {}
            rate = guards.float_field(payload.get("fundingRate"))
            if rate is not None:
                nxt = payload.get("nextSettleTime")
                try:
                    ts = int(nxt) if nxt else None
                except (TypeError, ValueError):
                    ts = None
                return rate, ts
        return None, None

    # --- OKX ---

    def _okx_px(self, data: Any) -> float | None:
        if isinstance(data, dict) and guards.okx_ok(data.get("code")):
            rows = data.get("data") or []
            if rows and rows[0].get("last"):
                return float(rows[0]["last"])
        return None

    async def okx_spot(self, base: str, quote: str) -> float | None:
        inst = await self._sym("okx", base, quote, "spot")
        spot_raw = await self._http.get_json(
            "https://www.okx.com/api/v5/market/ticker",
            params={"instId": inst},
        )
        return self._okx_px(spot_raw)

    async def okx_futures(self, base: str, quote: str) -> float | None:
        inst = await self._sym("okx", base, quote, "futures")
        fut_raw = await self._http.get_json(
            "https://www.okx.com/api/v5/market/ticker",
            params={"instId": inst},
        )
        return self._okx_px(fut_raw)

    async def okx_prices(self, base: str, quote: str) -> tuple[float | None, float | None]:
        return await self._pair_resilient("okx", self.okx_spot, self.okx_futures, base, quote)

    async def okx_funding(self, base: str, quote: str) -> tuple[float | None, int | None]:
        inst = await self._sym("okx", base, quote, "futures")
        data = await self._http.get_json(
            "https://www.okx.com/api/v5/public/funding-rate",
            params={"instId": inst},
        )
        if isinstance(data, dict) and guards.okx_ok(data.get("code")):
            rows = data.get("data") or []
            if rows:
                return float(rows[0].get("fundingRate", 0)), int(rows[0].get("nextFundingTime") or 0)
        return None, None

    # --- Bitget ---

    def _bitget_px(self, data: Any) -> float | None:
        return guards.list_row_price(
            (data or {}).get("data") if isinstance(data, dict) else None,
            "lastPr",
        )

    async def bitget_spot(self, base: str, quote: str) -> float | None:
        sym = await self._sym("bitget", base, quote, "spot")
        spot_raw = await self._http.get_json(
            "https://api.bitget.com/api/v2/spot/market/tickers",
            params={"symbol": sym},
        )
        return self._bitget_px(spot_raw)

    async def bitget_futures(self, base: str, quote: str) -> float | None:
        sym = await self._sym("bitget", base, quote, "futures")
        fut_raw = await self._http.get_json(
            "https://api.bitget.com/api/v2/mix/market/ticker",
            params={"symbol": sym, "productType": "USDT-FUTURES"},
        )
        return self._bitget_px(fut_raw)

    async def bitget_prices(self, base: str, quote: str) -> tuple[float | None, float | None]:
        return await self._pair_resilient("bitget", self.bitget_spot, self.bitget_futures, base, quote)

    async def bitget_funding(self, base: str, quote: str) -> tuple[float | None, int | None]:
        sym = await self._sym("bitget", base, quote, "futures")
        params = {"symbol": sym, "productType": "USDT-FUTURES"}
        data, tdata = await asyncio.gather(
            self._http.get_json(
                "https://api.bitget.com/api/v2/mix/market/current-fund-rate",
                params=params,
            ),
            self._http.get_json(
                "https://api.bitget.com/api/v2/mix/market/funding-time",
                params=params,
            ),
        )
        rate, ts = None, None
        if isinstance(data, dict):
            rows = data.get("data") or []
            if rows and rows[0].get("fundingRate") is not None:
                rate = float(rows[0]["fundingRate"])
        if isinstance(tdata, dict):
            rows = tdata.get("data") or []
            if rows and rows[0].get("nextFundingTime"):
                ts = int(rows[0]["nextFundingTime"])
        if rate is not None:
            return rate, ts
        return None, None

    # --- KuCoin ---

    async def kucoin_spot(self, base: str, quote: str) -> float | None:
        sym = await self._sym("kucoin", base, quote, "spot")
        spot_raw = await self._http.get_json(
            "https://api.kucoin.com/api/v1/market/orderbook/level1",
            params={"symbol": sym},
        )
        if isinstance(spot_raw, dict) and guards.kucoin_ok(spot_raw.get("code")):
            return guards.price_positive((spot_raw.get("data") or {}).get("price"))
        return None

    async def kucoin_futures(self, base: str, quote: str) -> float | None:
        fut_sym = await self._sym("kucoin", base, quote, "futures")
        fut_raw = await self._http.get_json(
            "https://api-futures.kucoin.com/api/v1/ticker",
            params={"symbol": fut_sym},
        )
        if isinstance(fut_raw, dict) and guards.kucoin_ok(fut_raw.get("code")):
            return guards.price_positive((fut_raw.get("data") or {}).get("price"))
        return None

    async def kucoin_prices(self, base: str, quote: str) -> tuple[float | None, float | None]:
        return await self._pair_resilient("kucoin", self.kucoin_spot, self.kucoin_futures, base, quote)

    async def kucoin_funding(self, base: str, quote: str) -> tuple[float | None, int | None]:
        sym = await self._sym("kucoin", base, quote, "futures")
        data, fr = await asyncio.gather(
            self._http.get_json(f"https://api-futures.kucoin.com/api/v1/contracts/{sym}"),
            self._http.get_json(
                "https://api-futures.kucoin.com/api/v1/funding-rate",
                params={"symbol": sym, "forward": "true"},
            ),
        )
        if isinstance(data, dict) and guards.kucoin_ok(data.get("code")):
            row = data.get("data") or {}
            rate = row.get("fundingFeeRate") or row.get("predictedFundingFeeRate")
            nxt = row.get("nextFundingRateDateTime") or row.get("fundingTime")
            if rate is not None:
                ts = int(nxt) if nxt else None
                if ts is not None and ts < 1_000_000_000_000:
                    ts = None
                return float(rate), ts
        if isinstance(fr, dict) and guards.kucoin_ok(fr.get("code")):
            row = fr.get("data") or {}
            if row.get("value") is not None:
                return float(row["value"]), int(row.get("fundingTime") or 0)
        return None, None

    # --- BingX ---

    async def bingx_spot(self, base: str, quote: str) -> float | None:
        pair = await self._sym("bingx", base, quote, "spot")
        spot_raw = await self._http.get_json(
            "https://open-api.bingx.com/openApi/spot/v1/ticker/24hr",
            params={"symbol": pair},
        )
        if isinstance(spot_raw, dict) and guards.code_ok(spot_raw.get("code"), 0, "0"):
            data = spot_raw.get("data")
            if isinstance(data, list):
                return guards.list_row_price(data, "lastPrice")
            if isinstance(data, dict):
                return guards.dict_field_price(data, "lastPrice")
        return None

    async def bingx_futures(self, base: str, quote: str) -> float | None:
        pair = await self._sym("bingx", base, quote, "futures")
        fut_raw = await self._http.get_json(
            "https://open-api.bingx.com/openApi/swap/v2/quote/price",
            params={"symbol": pair},
        )
        if isinstance(fut_raw, dict) and guards.code_ok(fut_raw.get("code"), 0, "0"):
            return guards.price_positive((fut_raw.get("data") or {}).get("price"))
        return None

    async def bingx_prices(self, base: str, quote: str) -> tuple[float | None, float | None]:
        return await self._pair_resilient("bingx", self.bingx_spot, self.bingx_futures, base, quote)

    async def bingx_funding(self, base: str, quote: str) -> tuple[float | None, int | None]:
        pair = await self._sym("bingx", base, quote, "futures")
        data = await self._http.get_json(
            "https://open-api.bingx.com/openApi/swap/v2/quote/premiumIndex",
            params={"symbol": pair},
        )
        if isinstance(data, dict):
            payload = data.get("data") or {}
            if payload.get("lastFundingRate") is not None:
                return float(payload["lastFundingRate"]), int(payload.get("nextFundingTime") or 0)
        return None, None

    # --- Kraken ---

    async def _kraken_futures_tickers(self) -> dict[str, dict[str, Any]]:
        global _kraken_fut_cache
        now = time.time()
        if _kraken_fut_cache and now - _kraken_fut_cache[0] < _KRAKEN_FUT_CACHE_TTL:
            return _kraken_fut_cache[1]
        data = await self._http.get_json(
            "https://futures.kraken.com/derivatives/api/v3/tickers",
            timeout=5,
        )
        out: dict[str, dict[str, Any]] = {}
        if isinstance(data, dict) and data.get("result") == "success":
            for row in data.get("tickers") or []:
                if isinstance(row, dict):
                    sym = str(row.get("symbol") or "")
                    if sym:
                        out[sym] = row
        _kraken_fut_cache = (now, out)
        return out

    async def kraken_spot(self, base: str, quote: str) -> float | None:
        pair = await self._sym("kraken", base, quote, "spot")
        if not pair:
            return None
        spot_raw = await self._http.get_json(
            "https://api.kraken.com/0/public/Ticker",
            params={"pair": pair},
        )
        if not isinstance(spot_raw, dict) or spot_raw.get("error"):
            return None
        for payload in (spot_raw.get("result") or {}).values():
            if isinstance(payload, dict):
                close = payload.get("c")
                if isinstance(close, list) and close:
                    return guards.price_positive(close[0])
        return None

    async def kraken_futures(self, base: str, quote: str) -> float | None:
        sym = await self._sym("kraken", base, quote, "futures")
        if not sym:
            return None
        row = (await self._kraken_futures_tickers()).get(sym)
        if not row:
            return None
        return guards.price_positive(row.get("markPrice") or row.get("last"))

    async def kraken_prices(self, base: str, quote: str) -> tuple[float | None, float | None]:
        return await self._pair_resilient(
            "kraken", self.kraken_spot, self.kraken_futures, base, quote
        )

    async def kraken_funding(self, base: str, quote: str) -> tuple[float | None, int | None]:
        sym = await self._sym("kraken", base, quote, "futures")
        if not sym:
            return None, None
        row = (await self._kraken_futures_tickers()).get(sym)
        if not row:
            return None, None
        rate = guards.float_field(row.get("fundingRate"))
        if rate is not None:
            if abs(rate) > 0.01:
                rate /= 100.0
            return rate, _estimate_next_funding_hourly_ms()
        return None, None

    # --- Aster ---

    async def aster_spot(self, base: str, quote: str) -> float | None:
        sym = await self._sym("aster", base, quote, "spot")
        spot_raw = await self._http.get_json(
            "https://sapi.asterdex.com/api/v3/ticker/price",
            params={"symbol": sym},
        )
        return guards.dict_field_price(spot_raw, "price")

    async def aster_futures(self, base: str, quote: str) -> float | None:
        sym = await self._sym("aster", base, quote, "futures")
        fut_raw = await self._http.get_json(
            "https://fapi.asterdex.com/fapi/v1/ticker/price",
            params={"symbol": sym},
        )
        return guards.dict_field_price(fut_raw, "price")

    async def aster_prices(self, base: str, quote: str) -> tuple[float | None, float | None]:
        return await self._pair_resilient("aster", self.aster_spot, self.aster_futures, base, quote)

    async def aster_funding(self, base: str, quote: str) -> tuple[float | None, int | None]:
        sym = await self._sym("aster", base, quote, "futures")
        data = await self._http.get_json(
            "https://fapi.asterdex.com/fapi/v1/premiumIndex",
            params={"symbol": sym},
        )
        if isinstance(data, dict) and data.get("lastFundingRate") is not None:
            return float(data["lastFundingRate"]), int(data.get("nextFundingTime") or 0)
        return None, None

    # --- Hyperliquid ---

    async def hyperliquid_futures_price(self, base: str, quote: str) -> float | None:
        if quote.upper() != "USDT":
            return None
        name = (await self._sym("hyperliquid", base, quote, "futures")).upper()
        row = (await self._hyperliquid_assets()).get(name)
        if not row:
            return None
        return guards.price_positive(row.get("markPx") or row.get("midPx"))

    async def hyperliquid_prices(self, base: str, quote: str) -> tuple[float | None, float | None]:
        fut = await self._fetch_leg_resilient(
            self.hyperliquid_futures_price,
            "hyperliquid",
            base,
            quote,
            "futures",
            attempts=_LEG_RETRY_ATTEMPTS,
        )
        return None, fut

    async def hyperliquid_funding(self, base: str, quote: str) -> tuple[float | None, int | None]:
        if quote.upper() != "USDT":
            return None, None
        target = (await self._sym("hyperliquid", base, quote, "futures")).upper()
        row = (await self._hyperliquid_assets()).get(target)
        if not row:
            return None, None
        rate = guards.float_field(row.get("funding"))
        if rate is not None:
            return rate, _estimate_next_funding_ms()
        return None, None


def _estimate_next_funding_ms() -> int:
    period = 8 * 3600
    now = int(time.time())
    return ((now // period) + 1) * period * 1000


def _estimate_next_funding_hourly_ms() -> int:
    period = 3600
    now = int(time.time())
    return ((now // period) + 1) * period * 1000


def apply_funding(ticker: MarketTicker, rate: float | None, ts: int | None) -> None:
    if rate is None:
        return
    ticker.funding_rate = rate
    if ts:
        ticker.next_funding_ts = ts
    else:
        ticker.next_funding_ts = _estimate_next_funding_ms()


_PRICE_FETCHERS: dict[str, Callable[[ExchangeMarketClient, str, str], Awaitable[tuple[float | None, float | None]]]] = {
    "binance": ExchangeMarketClient.binance_prices,
    "bybit": ExchangeMarketClient.bybit_prices,
    "gate": ExchangeMarketClient.gate_prices,
    "mexc": ExchangeMarketClient.mexc_prices,
    "okx": ExchangeMarketClient.okx_prices,
    "bitget": ExchangeMarketClient.bitget_prices,
    "kucoin": ExchangeMarketClient.kucoin_prices,
    "bingx": ExchangeMarketClient.bingx_prices,
    "kraken": ExchangeMarketClient.kraken_prices,
    "aster": ExchangeMarketClient.aster_prices,
    "hyperliquid": ExchangeMarketClient.hyperliquid_prices,
}

_SPOT_ONLY: dict[str, Callable[[ExchangeMarketClient, str, str], Awaitable[float | None]]] = {
    "binance": ExchangeMarketClient.binance_spot,
    "bybit": ExchangeMarketClient.bybit_spot,
    "gate": ExchangeMarketClient.gate_spot,
    "mexc": ExchangeMarketClient.mexc_spot,
    "okx": ExchangeMarketClient.okx_spot,
    "bitget": ExchangeMarketClient.bitget_spot,
    "kucoin": ExchangeMarketClient.kucoin_spot,
    "bingx": ExchangeMarketClient.bingx_spot,
    "kraken": ExchangeMarketClient.kraken_spot,
    "aster": ExchangeMarketClient.aster_spot,
}

_FUTURES_ONLY: dict[str, Callable[[ExchangeMarketClient, str, str], Awaitable[float | None]]] = {
    "binance": ExchangeMarketClient.binance_futures,
    "bybit": ExchangeMarketClient.bybit_futures,
    "gate": ExchangeMarketClient.gate_futures,
    "mexc": ExchangeMarketClient.mexc_futures,
    "okx": ExchangeMarketClient.okx_futures,
    "bitget": ExchangeMarketClient.bitget_futures,
    "kucoin": ExchangeMarketClient.kucoin_futures,
    "bingx": ExchangeMarketClient.bingx_futures,
    "kraken": ExchangeMarketClient.kraken_futures,
    "aster": ExchangeMarketClient.aster_futures,
    "hyperliquid": ExchangeMarketClient.hyperliquid_futures_price,
}

_FUNDING_FETCHERS: dict[str, Callable[[ExchangeMarketClient, str, str], Awaitable[tuple[float | None, int | None]]]] = {
    "binance": ExchangeMarketClient.binance_funding,
    "bybit": ExchangeMarketClient.bybit_funding,
    "okx": ExchangeMarketClient.okx_funding,
    "gate": ExchangeMarketClient.gate_funding,
    "mexc": ExchangeMarketClient.mexc_funding,
    "bitget": ExchangeMarketClient.bitget_funding,
    "kucoin": ExchangeMarketClient.kucoin_funding,
    "bingx": ExchangeMarketClient.bingx_funding,
    "kraken": ExchangeMarketClient.kraken_funding,
    "aster": ExchangeMarketClient.aster_funding,
    "hyperliquid": ExchangeMarketClient.hyperliquid_funding,
}
