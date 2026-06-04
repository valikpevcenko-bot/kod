"""Aiogram application factory."""

from __future__ import annotations

import asyncio

import structlog
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from crypto_bot.config.settings import get_settings
from crypto_bot.core.http import close_http, get_http
from crypto_bot.core.logging import setup_logging
from crypto_bot.handlers import errors, start
from crypto_bot.handlers.get import setup_get_router
from crypto_bot.services.market_service import MarketService
from crypto_bot.services.report_cache import ReportCache

logger = structlog.get_logger(__name__)


class BotApp:
    """Wires dispatcher, services, graceful shutdown."""

    def __init__(self) -> None:
        self.settings = get_settings()
        self.market = MarketService()
        self.cache = ReportCache()
        self.bot = Bot(
            token=self.settings.bot_token.get_secret_value(),
            default=DefaultBotProperties(parse_mode=ParseMode.HTML),
        )
        self.dp = Dispatcher()

    def _register_routers(self) -> None:
        self.dp.include_router(start.router)
        self.dp.include_router(setup_get_router(self.market, self.cache))
        self.dp.include_router(errors.router)

    async def on_startup(self) -> None:
        setup_logging(self.settings.log_level)
        print("⏳ Warming up exchanges (~3–5s)…", flush=True)
        await get_http().start()
        await self.market.warmup()
        if self.settings.cmc_key():
            print("✅ CMC API configured (contracts from CoinMarketCap)", flush=True)
        else:
            print("⚠️  CMC_API_KEY missing in .env — contracts: exchanges only", flush=True)
        logger.info("bot_ready", cmc=bool(self.settings.cmc_key()))
        print("✅ Bot ready. In Telegram: /get sol", flush=True)
        asyncio.create_task(self._prefetch_dw_background())

    async def _prefetch_dw_background(self) -> None:
        try:
            await self.market.prefetch_dw_cache()
            logger.debug("mexc_dw_warmup_done")
        except Exception:
            logger.debug("mexc_dw_warmup_bg_skip")

    async def on_shutdown(self) -> None:
        await self.market.close()
        await close_http()
        await self.bot.session.close()
        logger.info("bot_stopped")

    async def run(self) -> None:
        self._register_routers()
        self.dp.startup.register(self.on_startup)
        self.dp.shutdown.register(self.on_shutdown)
        await self.dp.start_polling(self.bot)
