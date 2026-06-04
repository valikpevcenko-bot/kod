"""Global error handler."""

from __future__ import annotations

import structlog
from aiogram import Router
from aiogram.types import ErrorEvent

logger = structlog.get_logger(__name__)
router = Router(name="errors")


@router.errors()
async def on_error(event: ErrorEvent) -> None:
    logger.exception("telegram_error", error=str(event.exception))
