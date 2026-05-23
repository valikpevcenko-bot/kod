"""
Telegram-бот: /get TICKER — цены ~1 сек, контракты и D/W догружаются в то же сообщение.
"""

import asyncio
import html
import logging
import sys
import time

from aiogram import Bot, Dispatcher, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandObject
from aiogram.types import Message

from config import require_token
from contracts import preload_binance_coins
from fast_prices import close_fast_session
from fetcher import fetch_all_fast, fetch_all_full
from formatter import format_report
from pool import close_all
from response_cache import get as cache_get, set as cache_set
from ticker_parser import parse_ticker
from wallet import close_wallet_session, warmup_cache

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
            cache_set(base, quote, text, snaps, cont, complete=True)
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

    cached = cache_get(base, quote)
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
        cache_set(base, quote, report, snapshots, contracts, complete=False)

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
