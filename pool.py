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
REQUEST_TIMEOUT_MS = 12_000

_instances: dict[str, ccxt.Exchange] = {}
_lock = asyncio.Lock()
_fetch_locks: dict[str, asyncio.Lock] = {}


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


def _fetch_lock(key: str) -> asyncio.Lock:
    if key not in _fetch_locks:
        _fetch_locks[key] = asyncio.Lock()
    return _fetch_locks[key]


async def fetch_ticker_safe(
    class_name: str,
    symbol: str,
    extra: Optional[dict] = None,
    timeout_sec: float = 12.0,
) -> Optional[dict]:
    """fetch_ticker с блокировкой — один запрос на инстанс биржи за раз."""
    ex = await get_exchange(class_name, extra)
    key = _cache_key(class_name, extra)
    async with _fetch_lock(key):
        return await asyncio.wait_for(ex.fetch_ticker(symbol), timeout=timeout_sec)


async def close_all() -> None:
    global _instances
    for ex in _instances.values():
        try:
            await ex.close()
        except Exception as exc:  # noqa: BLE001
            logger.debug("close %s: %s", ex.id, exc)
    _instances = {}
    _fetch_locks.clear()
