"""
Пул подключений CCXT — создаём биржи один раз, переиспользуем на каждый /get.
Это убирает ~15–20 секунд на повторные handshake/load_markets.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable, Optional

import ccxt.async_support as ccxt

from config import credentials_for

logger = logging.getLogger(__name__)

# Короткий таймаут: если биржа не ответила — идём дальше
REQUEST_TIMEOUT_MS = 8_000

_instances: dict[str, ccxt.Exchange] = {}
_lock = asyncio.Lock()


def _cache_key(class_name: str, extra: Optional[dict]) -> str:
    if not extra:
        return class_name
    opts = extra.get("options") or {}
    return f"{class_name}:{opts.get('defaultType', 'default')}"


def _build(class_name: str, extra: Optional[dict] = None) -> ccxt.Exchange:
    params: dict[str, Any] = {
        "enableRateLimit": True,
        "timeout": REQUEST_TIMEOUT_MS,
    }
    params.update(credentials_for(class_name))
    if extra:
        params.update(extra)
    exchange_class: Callable[..., ccxt.Exchange] = getattr(ccxt, class_name)
    return exchange_class(params)


async def get_exchange(class_name: str, extra: Optional[dict] = None) -> ccxt.Exchange:
    key = _cache_key(class_name, extra)
    if key in _instances:
        return _instances[key]

    async with _lock:
        if key in _instances:
            return _instances[key]
        exchange = _build(class_name, extra)
        _instances[key] = exchange
        return exchange


async def close_all() -> None:
    global _instances
    for ex in _instances.values():
        try:
            await ex.close()
        except Exception as exc:  # noqa: BLE001
            logger.debug("close %s: %s", ex.id, exc)
    _instances = {}
