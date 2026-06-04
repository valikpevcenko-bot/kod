""" /get command handler."""

from __future__ import annotations

import asyncio
import time

import structlog
from aiogram import Router
from aiogram.filters import Command, CommandObject
from aiogram.types import Message

from crypto_bot.config.settings import get_settings
from crypto_bot.domain.exchanges import FETCH_HARD_TIMEOUT, REPORT_EXCHANGE_COUNT
from crypto_bot.services.report_ui import needs_more_exchanges, with_loading_footer
from crypto_bot.domain.ticker import parse_ticker
from crypto_bot.services.market_service import MarketService
from crypto_bot.services.report_cache import ReportCache

logger = structlog.get_logger(__name__)
router = Router(name="get")

_FIRST_WAIT = 0.4


def setup_get_router(market: MarketService, cache: ReportCache) -> Router:
    settings = get_settings()

    async def _load_snapshots(
        base: str,
        quote: str,
        *,
        task: asyncio.Task | None = None,
    ) -> tuple[list, list]:
        coro = task if task is not None else asyncio.create_task(market.fetch_fast(base, quote))
        try:
            return await asyncio.wait_for(asyncio.shield(coro), timeout=FETCH_HARD_TIMEOUT)
        except asyncio.TimeoutError:
            logger.warning("fetch_hard_timeout", base=base)
            if task is not None:
                task.cancel()
            return [], []

    async def _deliver(
        base: str,
        quote: str,
        snapshots: list,
        contracts: list,
        *,
        status: Message | None,
        message: Message,
        t0: float,
    ) -> Message:
        report = market.report_text(base, quote, snapshots, contracts)
        ex_count = market._exchange_count(snapshots)
        if ex_count > 0 and needs_more_exchanges(ex_count, target=REPORT_EXCHANGE_COUNT):
            report = with_loading_footer(report)
        cache.set(base, quote, report, snapshots, contracts, complete=False)
        if status is not None:
            await status.edit_text(report, disable_web_page_preview=True)
            out = status
        else:
            out = await message.answer(report, disable_web_page_preview=True)
        logger.info(
            "fast_report",
            base=base,
            ms=int((time.perf_counter() - t0) * 1000),
            exchanges=len([s for s in snapshots if s.has_data]),
        )
        market.schedule_enrich(base, quote, snapshots, contracts, out, cache)
        return out

    @router.message(Command("get"))
    async def cmd_get(message: Message, command: CommandObject) -> None:
        if not command.args:
            await message.answer("Send a ticker: <code>/get sol</code>")
            return

        raw = command.args.strip().split()[0]
        try:
            base, quote = parse_ticker(raw, settings.default_quote)
        except ValueError as exc:
            await message.answer(f"❌ {exc}")
            return

        t0 = time.perf_counter()
        pair_key = f"{base.upper()}:{quote.upper()}"

        instant = market.peek_fast(base, quote)
        if instant:
            text = instant.text
            if needs_more_exchanges(
                market._exchange_count(instant.snapshots), target=REPORT_EXCHANGE_COUNT
            ):
                text = with_loading_footer(text)
            msg = await message.answer(text, disable_web_page_preview=True)
            logger.info("fast_cache_hit", base=base, ms=int((time.perf_counter() - t0) * 1000))
            if market._exchange_count(instant.snapshots) > 0 and (
                not instant.complete
                or needs_more_exchanges(
                    market._exchange_count(instant.snapshots), target=REPORT_EXCHANGE_COUNT
                )
            ):
                market.schedule_enrich(
                    base, quote, instant.snapshots, instant.contracts, msg, cache
                )
            return

        peek = cache.peek(base, quote)
        if peek:
            cached, stale = peek
            msg = await message.answer(cached.text, disable_web_page_preview=True)
            logger.info(
                "cache_hit",
                base=base,
                ms=int((time.perf_counter() - t0) * 1000),
                stale=stale,
                complete=cached.complete,
            )
            if (
                stale
                or not cached.complete
                or needs_more_exchanges(
                    market._exchange_count(cached.snapshots), target=REPORT_EXCHANGE_COUNT
                )
                or market._needs_enrich(cached.snapshots, cached.contracts, base)
            ):
                market.schedule_enrich(
                    base,
                    quote,
                    cached.snapshots,
                    cached.contracts,
                    msg,
                    cache,
                    refresh_prices=stale,
                )
            return

        inflight = market._inflight_fast.get(pair_key)
        fetch_task = inflight if inflight and not inflight.done() else asyncio.create_task(
            market.fetch_fast(base, quote)
        )
        if fetch_task is not inflight:
            market._inflight_fast[pair_key] = fetch_task

        status: Message | None = None
        try:
            try:
                snapshots, contracts = await asyncio.wait_for(
                    asyncio.shield(fetch_task), timeout=_FIRST_WAIT
                )
            except asyncio.TimeoutError:
                status = await message.answer(f"⏳ <b>{base}</b>…")
                snapshots, contracts = await _load_snapshots(
                    base, quote, task=fetch_task
                )

            await _deliver(
                base,
                quote,
                snapshots,
                contracts,
                status=status,
                message=message,
                t0=t0,
            )
        except Exception:
            logger.exception("get_error", base=base)
            err = "❌ Error. Please try again."
            if status is not None:
                await status.edit_text(err)
            else:
                await message.answer(err)
        finally:
            if market._inflight_fast.get(pair_key) is fetch_task:
                market._inflight_fast.pop(pair_key, None)

    return router
