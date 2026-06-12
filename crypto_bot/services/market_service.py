"""Orchestrates market data, wallets, contracts for /get."""

from __future__ import annotations

import asyncio
import time
from typing import Any, NamedTuple

import structlog

from crypto_bot.clients.coinmarketcap import filter_display_contracts
from crypto_bot.clients.exchanges.market import ExchangeMarketClient, apply_funding
from crypto_bot.config.settings import get_settings
from crypto_bot.domain import links
from crypto_bot.core.price_fast import price_fast_ctx
from crypto_bot.domain.exchanges import (
    CONTRACT_HTTP_TIMEOUT,
    ENRICH_JOB_TIMEOUT,
    ENRICH_WALLET_PHASE_TIMEOUT,
    ENRICH_PRICES_TIMEOUT,
    ENRICH_RETRY_TIMEOUT,
    EXCHANGE_BY_KEY,
    EXCHANGE_DEFS,
    ExchangeDef,
    FAST_BURST_MIN,
    FAST_BURST_TIMEOUT,
    FAST_EXCHANGE_TIMEOUT,
    FAST_FETCH_TIMEOUT,
    FAST_TIER_KEYS,
    FAST_TIER_MIN,
    FUNDING_BATCH_TIMEOUT,
    NOT_FOUND_CACHE_TTL,
    NOT_FOUND_GIVEUP,
    PER_EXCHANGE_TIMEOUT,
    EXCHANGE_PRICE_TIMEOUT,
    DEX_EXCHANGE_KEYS,
    REPORT_EXCHANGE_COUNT,
    RETRY_EXCHANGE_PRIORITY,
    SPOT_EXCHANGE_DEFS,
    TURBO_BACKFILL_TIMEOUT,
    TURBO_ENRICH_PRICES_TIMEOUT,
    TURBO_EXCHANGE_TIMEOUT_FAST,
    TURBO_EXCHANGE_TIMEOUT_SLOW,
    TURBO_FETCH_TIMEOUT,
    TURBO_FUNDING_TIMEOUT,
    TURBO_PRICE_CONTINUATION_TIMEOUT,
    TURBO_WALLET_PHASE_TIMEOUT,
)
from crypto_bot.services.report_ui import strip_loading_footer
from crypto_bot.services.snapshot_merge import coalesce_snapshot, merge_snapshots
from crypto_bot.services.ttl_cache import TtlCache
from crypto_bot.models.market import ContractInfo, ExchangeSnapshot, MarketTicker, WalletStatus
from crypto_bot.services.contracts import ContractService
from crypto_bot.clients.dw.registry import DW_EXCHANGE_KEYS
from crypto_bot.services.dw_service import (
    DwService,
    cache_get as wallet_cache_get,
    cache_set as wallet_cache_set,
    has_rows as wallet_has_rows,
)

logger = structlog.get_logger(__name__)

_PRICE_CONCURRENCY = 24
_EDIT_MIN_INTERVAL = 0.35
_EDIT_MIN_INTERVAL_TURBO = 0.08
_WALLET_PREFETCH_SYMBOLS = ("SOL", "BTC", "ETH", "USDT")


class FastBundle(NamedTuple):
    text: str
    snapshots: list[ExchangeSnapshot]
    contracts: list[ContractInfo]
    complete: bool = False


class MarketService:
    """Build exchange snapshots and enrich in background."""

    def __init__(
        self,
        market: ExchangeMarketClient | None = None,
        wallets: DwService | None = None,
        contracts: ContractService | None = None,
    ) -> None:
        self._market = market or ExchangeMarketClient()
        self._wallets = wallets or DwService()
        self._contracts = contracts or ContractService()
        self._settings = get_settings()
        self._fast_cache: TtlCache[FastBundle] = TtlCache(float(self._settings.fast_cache_ttl))
        self._miss_cache: TtlCache[FastBundle] = TtlCache(float(NOT_FOUND_CACHE_TTL))
        self._enriching: set[str] = set()
        self._pending_edits: dict[str, list[Any]] = {}
        self._inflight_fast: dict[str, asyncio.Task[tuple[list[ExchangeSnapshot], list[ContractInfo]]]] = {}
        self._bg_tasks: set[asyncio.Task] = set()
        self._enrich_tasks: dict[str, asyncio.Task] = {}
        self._price_continuations: dict[str, list[asyncio.Task]] = {}
        self._price_sem = asyncio.Semaphore(_PRICE_CONCURRENCY)
        self._last_edit: dict[str, tuple[str, float]] = {}
        self._wallet_prefetch: dict[str, asyncio.Task[None]] = {}

    def _pair_key(self, base: str, quote: str) -> str:
        return f"{base.upper()}:{quote.upper()}"

    def kick_wallet_prefetch(self, symbol: str) -> None:
        """Start D/W fetch in background — overlaps with price wave on Asia VPS."""
        sym = symbol.upper()
        existing = self._wallet_prefetch.get(sym)
        if existing is not None and not existing.done():
            return

        async def _run() -> None:
            keys = self._wallet_keys_to_prefetch(sym)
            if not keys:
                return
            fresh = await self._fetch_wallets_parallel(sym, keys)
            for key, wallet in fresh.items():
                if wallet_has_rows(wallet):
                    wallet_cache_set(key, sym, wallet)

        task = asyncio.create_task(_run())
        self._wallet_prefetch[sym] = task
        self._bg_tasks.add(task)
        task.add_done_callback(self._bg_tasks.discard)

    def kick_wallet_prefetch_many(self, symbols: tuple[str, ...] = _WALLET_PREFETCH_SYMBOLS) -> None:
        for sym in symbols:
            self.kick_wallet_prefetch(sym)

    def _wallet_keys_to_prefetch(self, sym: str) -> list[str]:
        keys: list[str] = []
        for defn in SPOT_EXCHANGE_DEFS:
            if defn.key not in DW_EXCHANGE_KEYS:
                continue
            cached = wallet_cache_get(defn.key, sym)
            if cached is not None and wallet_has_rows(cached):
                continue
            keys.append(defn.key)
        return keys

    async def prefetch_dw_cache(self) -> None:
        """Прогрів MEXC capital/config (важкий список монет)."""
        try:
            await asyncio.wait_for(self._wallets.prefetch_mexc_capital(), timeout=20.0)
        except Exception:
            logger.debug("mexc_dw_warmup_skip")

    async def warmup(self) -> None:
        from crypto_bot.core.http import get_http

        http = get_http()
        await asyncio.gather(
            self._contracts.preload_binance(),
            self._market.preload_resolvers(
                [
                    "binance",
                    "bybit",
                    "bitget",
                    "mexc",
                    "gate",
                    "kucoin",
                    "kraken",
                    "okx",
                    "bingx",
                    "aster",
                    "hyperliquid",
                ]
            ),
            self._market.warm_mexc_futures_map(),
            self._market.warm_hyperliquid_assets(),
            self._market.warm_kraken_futures_tickers(),
            http.get_json("https://api.binance.com/api/v3/ping"),
            return_exceptions=True,
        )

    def peek_fast(self, base: str, quote: str) -> FastBundle | None:
        key = self._pair_key(base, quote)
        hit = self._fast_cache.get(key)
        if hit is not None and hit.complete:
            return hit
        miss = self._miss_cache.get(key)
        return miss

    def report_text(
        self,
        base: str,
        quote: str,
        snapshots: list[ExchangeSnapshot],
        contracts: list[ContractInfo],
    ) -> str:
        from crypto_bot.services.formatter import format_report_safe

        return format_report_safe(base, quote, snapshots, contracts)

    async def fetch_fast(
        self,
        base: str,
        quote: str,
    ) -> tuple[list[ExchangeSnapshot], list[ContractInfo]]:
        key = self._pair_key(base, quote)
        cached = self._fast_cache.get(key)
        if cached is not None:
            return cached.snapshots, cached.contracts
        self._miss_cache.delete(key)

        sym = base.upper()
        self.kick_wallet_prefetch(sym)
        turbo = self._settings.turbo_mode
        token = price_fast_ctx.set(True)
        try:
            if turbo:
                snapshots, contracts = await self._fetch_turbo_wave(base, quote, sym)
            else:
                snapshots, contracts = await self._fetch_standard_fast(base, quote, sym)
        finally:
            price_fast_ctx.reset(token)

        self._attach_wallets_cache_only(snapshots, sym)

        from crypto_bot.services.formatter import format_report_safe

        text = format_report_safe(base, quote, snapshots, contracts)
        complete = self._report_complete(snapshots, contracts, base)
        bundle = FastBundle(
            text=text,
            snapshots=snapshots,
            contracts=contracts,
            complete=complete,
        )
        if self._exchange_count(snapshots) > 0:
            self._fast_cache.set(key, bundle)
        return snapshots, contracts

    async def _fetch_standard_fast(
        self,
        base: str,
        quote: str,
        sym: str,
    ) -> tuple[list[ExchangeSnapshot], list[ContractInfo]]:
        try:
            snapshots = await asyncio.wait_for(
                self._fetch_burst(base, quote, fast=True),
                timeout=FAST_FETCH_TIMEOUT,
            )
        except asyncio.TimeoutError:
            snapshots = []

        try:
            await asyncio.wait_for(
                self._backfill_snapshot_legs(snapshots, base, quote),
                timeout=2.0,
            )
        except asyncio.TimeoutError:
            pass

        contracts = filter_display_contracts(self._contracts.cached(sym), coin=sym) or []
        return snapshots, contracts

    async def _fetch_turbo_wave(
        self,
        base: str,
        quote: str,
        sym: str,
    ) -> tuple[list[ExchangeSnapshot], list[ContractInfo]]:
        """Progressive paint: первые биржи за ~FIRST_PAINT_MS, остальные — в enrich."""
        pair_key = self._pair_key(base, quote)
        wall = min(
            max(self._settings.first_response_sec, 0.7),
            TURBO_FETCH_TIMEOUT + 0.12,
        )
        first_paint = max(
            self._settings.first_paint_ms / 1000.0,
            0.20 if self._settings.asia_vps else 0.22,
        )
        listed = [d.key for d in SPOT_EXCHANGE_DEFS]
        contracts_cached = filter_display_contracts(self._contracts.cached(sym), coin=sym)

        by_key: dict[str, ExchangeSnapshot] = {}
        price_tasks = [
            asyncio.create_task(self._fetch_one(d, base, quote, fast=True))
            for d in EXCHANGE_DEFS
        ]
        pending = set(price_tasks)
        contracts_box: list[list[ContractInfo]] = [contracts_cached or []]
        contract_task: asyncio.Task | None = None

        async def _load_contracts() -> None:
            fetched = await self._contracts.fetch(base, listed_on=listed)
            contracts_box[0] = filter_display_contracts(fetched, coin=sym) or fetched

        if not contracts_cached:
            contract_task = asyncio.create_task(_load_contracts())

        started = time.monotonic()
        while pending:
            elapsed = time.monotonic() - started
            if elapsed >= wall:
                break
            if len(by_key) >= 8:
                break
            if elapsed >= first_paint:
                break
            tick = min(0.05, wall - elapsed, first_paint - elapsed)
            if tick <= 0:
                break
            done, pending = await asyncio.wait(
                pending, timeout=tick, return_when=asyncio.FIRST_COMPLETED
            )
            for task in done:
                try:
                    snap = task.result()
                    if snap.has_data:
                        by_key[snap.key] = coalesce_snapshot(
                            by_key.get(snap.key), snap
                        )
                except Exception:
                    pass

        if pending:
            self._price_continuations[pair_key] = list(pending)

        if contract_task is not None and not contract_task.done():
            try:
                await asyncio.wait_for(contract_task, timeout=0.04)
            except asyncio.TimeoutError:
                pass

        snapshots = self._merge(by_key)
        contracts = contracts_box[0]
        if not contracts:
            contracts = filter_display_contracts(self._contracts.cached(sym), coin=sym) or []
        return snapshots, contracts

    async def _absorb_price_continuations(
        self,
        pair_key: str,
        by_key: dict[str, ExchangeSnapshot],
        *,
        timeout: float = TURBO_PRICE_CONTINUATION_TIMEOUT,
    ) -> None:
        tasks = self._price_continuations.pop(pair_key, [])
        if not tasks:
            return
        done, pending = await asyncio.wait(set(tasks), timeout=timeout)
        for task in done:
            try:
                snap = task.result()
                if isinstance(snap, ExchangeSnapshot) and snap.has_data:
                    by_key[snap.key] = coalesce_snapshot(by_key.get(snap.key), snap)
            except Exception:
                pass
        for task in pending:
            task.cancel()

    async def enrich(
        self,
        base: str,
        quote: str,
        snapshots: list[ExchangeSnapshot],
        contracts: list[ContractInfo],
        *,
        refresh_prices: bool = False,
        on_update: Any = None,
    ) -> tuple[list[ExchangeSnapshot], list[ContractInfo], str]:
        sym = base.upper()
        pair_key = self._pair_key(base, quote)
        snaps = list(snapshots)
        cont = list(contracts)
        from crypto_bot.services.formatter import format_report_safe

        if refresh_prices:
            fresh_snaps, fresh_cont = await self.fetch_fast(base, quote)
            snaps = merge_snapshots(snaps, fresh_snaps)
            if fresh_cont and not cont:
                cont = fresh_cont

        wallet_keys = self._wallet_keys_to_fetch(snaps, sym)
        self.kick_wallet_prefetch(sym)
        wallet_task = asyncio.create_task(
            self._fetch_wallets_parallel(sym, wallet_keys)
        )
        contracts_task = asyncio.create_task(self._load_contracts(base, sym, cont))
        prices_task = asyncio.create_task(self._fill_missing_exchanges(snaps, base, quote))

        snaps, cont = await asyncio.gather(prices_task, contracts_task)
        await self._ensure_funding(snaps, base, quote)
        self._attach_wallets_cache_only(snaps, sym)

        split = self._settings.turbo_mode and self._settings.enrich_split
        if split and on_update:
            mid_text = format_report_safe(base, quote, snaps, cont)
            await self._push_edit(
                pair_key,
                on_update,
                mid_text,
                snaps,
                cont,
                complete=False,
            )

        wallet_limit = (
            TURBO_WALLET_PHASE_TIMEOUT
            if self._settings.turbo_mode
            else ENRICH_WALLET_PHASE_TIMEOUT
        )
        try:
            fresh_wallets = await asyncio.wait_for(wallet_task, timeout=wallet_limit)
        except asyncio.TimeoutError:
            logger.warning("enrich_wallet_phase_timeout", base=base)
            fresh_wallets = {}
        for key, wallet in fresh_wallets.items():
            if wallet_has_rows(wallet):
                wallet_cache_set(key, sym, wallet)
        self._attach_wallets(snaps, sym, fresh_wallets)
        await self._ensure_funding(snaps, base, quote)

        text = format_report_safe(base, quote, snaps, cont)
        complete = self._report_complete(snaps, cont, base)
        if on_update:
            await self._push_edit(pair_key, on_update, text, snaps, cont, complete=complete)
        bundle = FastBundle(text=text, snapshots=snaps, contracts=cont, complete=complete)
        if self._exchange_count(snaps) > 0:
            self._fast_cache.set(pair_key, bundle)
        elif complete:
            self._miss_cache.set(pair_key, bundle)
        return snaps, cont, text

    async def _push_edit(
        self,
        pair_key: str,
        on_update: Any,
        text: str,
        snaps: list[ExchangeSnapshot],
        cont: list[ContractInfo],
        *,
        complete: bool,
    ) -> None:
        prev = self._last_edit.get(pair_key)
        now = time.monotonic()
        if prev and strip_loading_footer(prev[0]) == strip_loading_footer(text):
            return
        min_gap = (
            _EDIT_MIN_INTERVAL_TURBO
            if self._settings.turbo_mode
            else _EDIT_MIN_INTERVAL
        )
        if prev and now - prev[1] < min_gap:
            await asyncio.sleep(min_gap - (now - prev[1]))
        self._last_edit[pair_key] = (text, time.monotonic())
        await on_update(text, snaps, cont, complete=complete)

    async def _load_contracts(
        self,
        base: str,
        sym: str,
        existing: list[ContractInfo],
    ) -> list[ContractInfo]:
        if existing:
            return existing
        try:
            listed = [d.key for d in SPOT_EXCHANGE_DEFS]
            fetched = await asyncio.wait_for(
                self._contracts.fetch(base, listed_on=listed),
                timeout=CONTRACT_HTTP_TIMEOUT,
            )
            return filter_display_contracts(fetched, coin=sym) or fetched
        except (asyncio.TimeoutError, Exception):
            logger.debug("contracts_fetch_skip", symbol=sym)
            return filter_display_contracts(self._contracts.cached(sym), coin=sym) or []

    def schedule_enrich(
        self,
        base: str,
        quote: str,
        snapshots: list[ExchangeSnapshot],
        contracts: list[ContractInfo],
        message: Any,
        cache: Any,
        *,
        refresh_prices: bool = False,
    ) -> None:
        pair_key = self._pair_key(base, quote)
        self._pending_edits.setdefault(pair_key, []).append(message)
        prev_task = self._enrich_tasks.get(pair_key)
        if prev_task and not prev_task.done():
            prev_task.cancel()
        if pair_key in self._enriching:
            self._enriching.discard(pair_key)
        if not refresh_prices and not self._needs_enrich(snapshots, contracts, base):
            pending = self._pending_edits.pop(pair_key, None) or []
            if pending:
                text = self.report_text(base, quote, snapshots, contracts)
                asyncio.create_task(self._edit_messages(pair_key, pending, text))
            return
        self._enriching.add(pair_key)

        async def _job() -> None:
            updated = False
            latest: dict[str, Any] = {
                "snaps": list(snapshots),
                "cont": list(contracts),
                "text": None,
            }

            async def _edit(text: str, snaps: list, cont: list, *, complete: bool) -> None:
                nonlocal updated
                updated = True
                latest["snaps"] = snaps
                latest["cont"] = cont
                body = strip_loading_footer(text)
                latest["text"] = body
                cache.set(base, quote, body, snaps, cont, complete=complete)
                targets = list(self._pending_edits.get(pair_key, []))
                await self._edit_messages(pair_key, targets, body, force=True)
                logger.info(
                    "enrich_edit",
                    base=base,
                    exchanges=sum(1 for s in snaps if s.has_data),
                    complete=complete,
                )

            try:
                await asyncio.wait_for(
                    self.enrich(
                        base,
                        quote,
                        snapshots,
                        contracts,
                        refresh_prices=refresh_prices,
                        on_update=_edit,
                    ),
                    timeout=ENRICH_JOB_TIMEOUT,
                )
            except asyncio.TimeoutError:
                logger.warning("enrich_timeout", base=base)
            except Exception:
                logger.exception("enrich_failed", base=base)
            finally:
                pending = self._pending_edits.pop(pair_key, None) or []
                self._enriching.discard(pair_key)
                self._enrich_tasks.pop(pair_key, None)
                self._last_edit.pop(pair_key, None)
                if pending:
                    fallback = latest["text"] or self.report_text(
                        base,
                        quote,
                        latest["snaps"],
                        latest["cont"],
                    )
                    complete = self._report_complete(
                        latest["snaps"], latest["cont"], base
                    )
                    cache.set(
                        base,
                        quote,
                        fallback,
                        latest["snaps"],
                        latest["cont"],
                        complete=complete,
                    )
                    await self._edit_messages(pair_key, pending, fallback, force=True)

        def _done(t: asyncio.Task) -> None:
            self._bg_tasks.discard(t)
            if not t.cancelled():
                exc = t.exception()
                if exc and not isinstance(exc, asyncio.CancelledError):
                    logger.error("enrich_task_crash", base=base, error=str(exc)[:120])

        task = asyncio.create_task(_job())
        self._enrich_tasks[pair_key] = task
        self._bg_tasks.add(task)
        task.add_done_callback(_done)

    async def close(self) -> None:
        for task in list(self._bg_tasks):
            task.cancel()
        await self._wallets.close()

    def _burst_count(self, snapshots: list[ExchangeSnapshot]) -> int:
        by_key = {s.key: s for s in snapshots if s.has_data}
        return sum(1 for k in FAST_TIER_KEYS if by_key.get(k) and by_key[k].has_data)

    def _exchange_count(self, snapshots: list[ExchangeSnapshot]) -> int:
        return sum(1 for s in snapshots if s.has_data)

    def _report_complete(
        self,
        snapshots: list[ExchangeSnapshot],
        contracts: list[ContractInfo],
        base: str,
    ) -> bool:
        return not self._needs_enrich(snapshots, contracts, base)

    def _needs_enrich(
        self,
        snapshots: list[ExchangeSnapshot],
        contracts: list[ContractInfo],
        base: str,
    ) -> bool:
        if self._exchange_count(snapshots) < REPORT_EXCHANGE_COUNT:
            return True
        if self._burst_count(snapshots) < FAST_TIER_MIN:
            return True
        if not self._all_exchanges_ready(snapshots):
            return True
        if self._needs_funding(snapshots):
            return True
        if not contracts:
            return True
        return self._needs_wallet_fill(snapshots, base)

    def _wallet_keys_to_fetch(self, snapshots: list[ExchangeSnapshot], sym: str) -> list[str]:
        by_key = {s.key: s for s in snapshots}
        keys: list[str] = []
        for defn in SPOT_EXCHANGE_DEFS:
            if defn.key not in DW_EXCHANGE_KEYS:
                continue
            snap = by_key.get(defn.key)
            if snap is not None and not snap.has_data:
                continue
            cached = wallet_cache_get(defn.key, sym)
            if cached is not None and wallet_has_rows(cached):
                continue
            keys.append(defn.key)
        return keys

    async def _fetch_one(
        self,
        defn: ExchangeDef,
        base: str,
        quote: str,
        *,
        fast: bool = False,
    ) -> ExchangeSnapshot:
        if fast:
            if self._settings.turbo_mode:
                if defn.key in FAST_TIER_KEYS:
                    limit = TURBO_EXCHANGE_TIMEOUT_FAST
                elif defn.key in DEX_EXCHANGE_KEYS or defn.key == "gate":
                    limit = TURBO_EXCHANGE_TIMEOUT_SLOW
                elif defn.key == "kraken":
                    limit = 0.95
                else:
                    limit = 0.72
            else:
                limit = FAST_EXCHANGE_TIMEOUT
        else:
            limit = EXCHANGE_PRICE_TIMEOUT.get(defn.key, PER_EXCHANGE_TIMEOUT)
        try:
            async with self._price_sem:
                return await asyncio.wait_for(
                    self._fetch_one_inner(defn, base, quote, fast=fast),
                    timeout=limit,
                )
        except asyncio.TimeoutError:
            logger.debug("price_timeout", exchange=defn.key, symbol=base)
            return ExchangeSnapshot(
                key=defn.key,
                name=defn.name,
                futures_only=defn.futures_only,
            )

    async def _fetch_one_inner(
        self,
        defn: ExchangeDef,
        base: str,
        quote: str,
        *,
        fast: bool = False,
    ) -> ExchangeSnapshot:
        snap = ExchangeSnapshot(
            key=defn.key,
            name=defn.name,
            futures_only=defn.futures_only,
        )
        try:
            spot_p, fut_p = await self._market.spot_and_futures(
                defn.key, base, quote, fast=fast
            )
        except Exception as exc:
            logger.debug("price_error", exchange=defn.key, error=str(exc)[:80])
            return snap

        if spot_p is not None:
            snap.spot = MarketTicker(price=spot_p, url=links.spot_url(defn.key, base, quote))
        if fut_p is not None:
            snap.futures = MarketTicker(
                price=fut_p, url=links.futures_url(defn.key, base, quote)
            )
        return snap

    async def _fill_missing_exchanges(
        self,
        snapshots: list[ExchangeSnapshot],
        base: str,
        quote: str,
    ) -> list[ExchangeSnapshot]:
        pair_key = self._pair_key(base, quote)
        by_key = {s.key: s for s in snapshots}
        if self._settings.turbo_mode:
            await self._absorb_price_continuations(pair_key, by_key)

        missing = [
            EXCHANGE_BY_KEY[d.key]
            for d in EXCHANGE_DEFS
            if d.key not in by_key or not by_key[d.key].has_data
        ]
        enrich_prices_t = (
            TURBO_ENRICH_PRICES_TIMEOUT
            if self._settings.turbo_mode
            else ENRICH_PRICES_TIMEOUT
        )
        if missing:
            await self._run_price_batch(
                missing, base, quote, by_key, enrich_prices_t, cancel_pending=False
            )

        retry = [
            EXCHANGE_BY_KEY[k]
            for k in RETRY_EXCHANGE_PRIORITY
            if k in EXCHANGE_BY_KEY and (k not in by_key or not by_key[k].has_data)
        ]
        if retry:
            await self._run_price_batch(
                retry, base, quote, by_key, ENRICH_RETRY_TIMEOUT, cancel_pending=True
            )

        if not self._settings.turbo_mode:
            gate = by_key.get("gate")
            if gate is None or not gate.has_data:
                extra = await self._fetch_one(
                    EXCHANGE_BY_KEY["gate"], base, quote, fast=False
                )
                by_key["gate"] = coalesce_snapshot(by_key.get("gate"), extra)

            for key in ("okx", *DEX_EXCHANGE_KEYS):
                snap = by_key.get(key)
                if snap is None or not snap.has_data:
                    defn = EXCHANGE_BY_KEY.get(key)
                    if defn:
                        extra = await self._fetch_one(defn, base, quote, fast=False)
                        by_key[key] = coalesce_snapshot(by_key.get(key), extra)

        snaps = self._merge(by_key)
        backfill_t = 1.2 if self._settings.turbo_mode else 2.5
        try:
            await asyncio.wait_for(
                self._backfill_snapshot_legs(snaps, base, quote),
                timeout=backfill_t,
            )
        except asyncio.TimeoutError:
            pass
        if self._settings.turbo_mode:
            try:
                await self._ensure_funding(
                    snaps, base, quote, timeout=TURBO_FUNDING_TIMEOUT
                )
            except asyncio.TimeoutError:
                pass
        return snaps

    async def _run_price_batch(
        self,
        defns: list[ExchangeDef],
        base: str,
        quote: str,
        by_key: dict[str, ExchangeSnapshot],
        timeout: float,
        *,
        fast: bool = False,
        cancel_pending: bool = True,
    ) -> None:
        tasks = [
            asyncio.create_task(self._fetch_one(d, base, quote, fast=fast))
            for d in defns
        ]
        done, pending = await asyncio.wait(tasks, timeout=timeout)
        if cancel_pending:
            for task in pending:
                task.cancel()
        elif pending:
            extra = await asyncio.wait(set(pending), timeout=0.8)
            done = set(done) | extra[0]
            for task in extra[1]:
                task.cancel()
        for task in done:
            try:
                item = task.result()
                if isinstance(item, ExchangeSnapshot):
                    by_key[item.key] = coalesce_snapshot(by_key.get(item.key), item)
            except Exception:
                pass

    async def _fetch_burst(
        self,
        base: str,
        quote: str,
        *,
        fast: bool = False,
    ) -> list[ExchangeSnapshot]:
        burst = [d for d in EXCHANGE_DEFS if d.key in FAST_TIER_KEYS]
        tasks = [
            asyncio.create_task(self._fetch_one(d, base, quote, fast=fast))
            for d in burst
        ]
        by_key: dict[str, ExchangeSnapshot] = {}
        pending = set(tasks)
        started = time.monotonic()
        deadline = started + FAST_BURST_TIMEOUT
        finished = 0
        total = len(tasks)

        while pending:
            remain = deadline - time.monotonic()
            if remain <= 0:
                break
            done, pending = await asyncio.wait(
                pending, timeout=remain, return_when=asyncio.FIRST_COMPLETED
            )
            finished += len(done)
            for task in done:
                try:
                    snap = task.result()
                    if snap.has_data:
                        by_key[snap.key] = coalesce_snapshot(by_key.get(snap.key), snap)
                except Exception:
                    pass
            if len(by_key) >= FAST_BURST_MIN:
                break
            if not by_key and finished >= total:
                break
            if not by_key and time.monotonic() - started >= NOT_FOUND_GIVEUP:
                break

        for task in pending:
            task.cancel()
        return self._merge(by_key)

    @staticmethod
    def _all_exchanges_ready(snapshots: list[ExchangeSnapshot]) -> bool:
        by_key = {s.key: s for s in snapshots if s.has_data}
        return all(by_key.get(d.key) and by_key[d.key].has_data for d in EXCHANGE_DEFS)

    async def _backfill_snapshot_legs(
        self,
        snapshots: list[ExchangeSnapshot],
        base: str,
        quote: str,
        *,
        funding_map: dict[str, tuple[float | None, int | None]] | None = None,
    ) -> None:
        from crypto_bot.clients.exchanges.market import _FUNDING_FETCHERS

        async def _fill(snap: ExchangeSnapshot) -> None:
            defn = EXCHANGE_BY_KEY.get(snap.key)
            if not defn or defn.futures_only or not snap.has_data:
                return
            if snap.spot is not None and snap.futures is not None:
                return
            if snap.spot is None:
                px = await self._market.fetch_spot_only(snap.key, base, quote)
                if px is not None:
                    snap.spot = MarketTicker(price=px, url=links.spot_url(snap.key, base, quote))
            if snap.futures is None:
                px = await self._market.fetch_futures_only(snap.key, base, quote)
                if px is not None:
                    fut = MarketTicker(price=px, url=links.futures_url(snap.key, base, quote))
                    if snap.key in _FUNDING_FETCHERS:
                        fr = funding_map.get(snap.key) if funding_map else None
                        if fr is None:
                            fr = await self._market.funding(snap.key, base, quote)
                        if fr:
                            apply_funding(fut, fr[0], fr[1])
                    snap.futures = fut

        to_fill = [
            s
            for s in snapshots
            if (d := EXCHANGE_BY_KEY.get(s.key))
            and not d.futures_only
            and s.has_data
            and (s.spot is None) != (s.futures is None)
        ]
        if to_fill:
            await asyncio.gather(*[_fill(s) for s in to_fill])

    async def _fetch_wallets_parallel(
        self,
        symbol: str,
        exchange_keys: list[str],
    ) -> dict[str, WalletStatus]:
        if not exchange_keys:
            return {}

        timeouts: dict[str, float] = {
            "mexc": 11.0 if self._settings.asia_vps else 14.0,
            "gate": 11.0 if self._settings.asia_vps else 14.0,
            "kraken": 6.0,
            "kucoin": 6.0,
        }
        default_t = 5.0 if self._settings.asia_vps else 6.0

        async def _one(key: str) -> tuple[str, WalletStatus]:
            hit = wallet_cache_get(key, symbol)
            if hit is not None and wallet_has_rows(hit):
                return key, hit
            limit = timeouts.get(key, default_t)
            try:
                wallet = await asyncio.wait_for(
                    self._wallets.fetch_bounded(key, symbol),
                    timeout=limit,
                )
                return key, wallet
            except asyncio.TimeoutError:
                logger.warning("wallet_exchange_timeout", exchange=key, symbol=symbol)
                return key, wallet_cache_get(key, symbol) or WalletStatus()

        pairs = await asyncio.gather(
            *[_one(key) for key in exchange_keys],
            return_exceptions=True,
        )
        fresh: dict[str, WalletStatus] = {}
        for item in pairs:
            if isinstance(item, tuple) and len(item) == 2:
                fresh[item[0]] = item[1]
        return fresh

    def _merge(self, by_key: dict[str, ExchangeSnapshot]) -> list[ExchangeSnapshot]:
        return [by_key[d.key] for d in EXCHANGE_DEFS if d.key in by_key]

    async def _ensure_funding(
        self,
        snapshots: list[ExchangeSnapshot],
        base: str,
        quote: str,
        *,
        timeout: float | None = None,
    ) -> None:
        from crypto_bot.clients.exchanges.market import _FUNDING_FETCHERS

        need_keys = [
            s.key
            for s in snapshots
            if s.futures
            and s.futures.funding_rate is None
            and s.key in _FUNDING_FETCHERS
        ]
        if not need_keys:
            return
        batch_t = timeout if timeout is not None else FUNDING_BATCH_TIMEOUT
        funding_map = await self._market.funding_map(
            base, quote, keys=need_keys, timeout=batch_t
        )
        self._apply_funding(snapshots, funding_map)
        still = [
            s.key
            for s in snapshots
            if s.futures and s.futures.funding_rate is None and s.key in _FUNDING_FETCHERS
        ]
        if still:

            async def _one(key: str) -> None:
                fr = await self._market.funding(key, base, quote)
                if fr[0] is None:
                    return
                snap = next((x for x in snapshots if x.key == key), None)
                if snap and snap.futures:
                    apply_funding(snap.futures, fr[0], fr[1])

            await asyncio.gather(*[_one(k) for k in still])

    def _apply_funding(
        self,
        snapshots: list[ExchangeSnapshot],
        funding_map: dict[str, tuple[float | None, int | None]],
    ) -> None:
        for snap in snapshots:
            if not snap.futures:
                continue
            fr = funding_map.get(snap.key)
            if fr:
                apply_funding(snap.futures, fr[0], fr[1])

    def _needs_funding(self, snapshots: list[ExchangeSnapshot]) -> bool:
        from crypto_bot.clients.exchanges.market import _FUNDING_FETCHERS

        return any(
            snap.futures
            and snap.futures.funding_rate is None
            and snap.key in _FUNDING_FETCHERS
            for snap in snapshots
        )

    def _attach_wallets_cache_only(
        self,
        snapshots: list[ExchangeSnapshot],
        base: str,
    ) -> None:
        for snap in snapshots:
            if snap.futures_only:
                continue
            if snap.key in DW_EXCHANGE_KEYS:
                snap.wallet = wallet_cache_get(snap.key, base)

    def _attach_wallets(
        self,
        snapshots: list[ExchangeSnapshot],
        base: str,
        fresh: dict[str, WalletStatus] | None,
    ) -> None:
        for snap in snapshots:
            if snap.futures_only:
                continue
            w = (fresh or {}).get(snap.key)
            if w is None:
                w = wallet_cache_get(snap.key, base)
            snap.wallet = w

    def _needs_wallet_fill(self, snapshots: list[ExchangeSnapshot], base: str) -> bool:
        for snap in snapshots:
            if snap.futures_only or not snap.has_data:
                continue
            if snap.key not in DW_EXCHANGE_KEYS:
                continue
            hit = snap.wallet or wallet_cache_get(snap.key, base)
            if not hit or not wallet_has_rows(hit):
                return True
        return False

    async def _edit_messages(
        self,
        pair_key: str,
        messages: list[Any],
        text: str,
        *,
        force: bool = False,
    ) -> None:
        from aiogram.exceptions import TelegramBadRequest

        body = strip_loading_footer(text)
        prev = self._last_edit.get(pair_key)
        if (
            not force
            and prev
            and strip_loading_footer(prev[0]) == body
        ):
            return
        self._last_edit[pair_key] = (body, time.monotonic())

        for msg in messages:
            try:
                await msg.edit_text(body, disable_web_page_preview=True)
            except TelegramBadRequest as exc:
                err = str(exc).lower()
                if "message is not modified" not in err and "not found" not in err:
                    logger.debug("edit_failed", error=str(exc)[:80])
