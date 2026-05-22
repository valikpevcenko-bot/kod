"""
Telegram-бот: /get TICKER — цены и статусы D/W с бирж.
"""

import asyncio
import html
import logging
import sys

from aiogram import Bot, Dispatcher, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandObject
from aiogram.types import Message

from config import require_token
from fetcher import fetch_all
from formatter import format_report
from pool import close_all
from ticker_parser import parse_ticker
from wallet import close_wallet_session, warmup_cache

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger(__name__)

router = Router()


@router.message(Command("start", "help"))
async def cmd_start(message: Message) -> None:
    await message.answer(
        "👋 <b>Crypto Price Bot</b>\n\n"
        "Команда: <code>/get stx</code> или <code>/get BTCUSDT</code>\n\n"
        "Показываю Spot, Futures, депозиты и выводы на 11 биржах.",
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

    status = await message.answer(
        f"⏳ <b>{html.escape(base)}</b>…",
        parse_mode=ParseMode.HTML,
    )

    try:
        snapshots, contracts = await fetch_all(base, quote)
        report = format_report(base, quote, snapshots, contracts)
        await status.edit_text(
            report,
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
        )
    except Exception:
        logger.exception("get %s", base)
        await status.edit_text("❌ Ошибка загрузки. Попробуй ещё раз.", parse_mode=ParseMode.HTML)


async def on_startup() -> None:
    # Кэш D/W в фоне — не блокирует старт
    asyncio.create_task(warmup_cache())
    logger.info("Бот готов (кэш бирж подгружается в фоне)")


async def on_shutdown() -> None:
    await close_wallet_session()
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
