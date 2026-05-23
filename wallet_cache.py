"""Кэш D/W — не запрашиваем биржи на каждый /get."""

from __future__ import annotations

import time
from typing import Optional

from models import WalletStatus

TTL = 300
_store: dict[str, tuple[float, WalletStatus]] = {}


def _key(exchange: str, symbol: str) -> str:
    return f"{exchange}:{symbol.upper()}"


def get(exchange: str, symbol: str) -> Optional[WalletStatus]:
    item = _store.get(_key(exchange, symbol))
    if not item:
        return None
    if time.time() - item[0] > TTL:
        return None
    return item[1]


def set(exchange: str, symbol: str, status: WalletStatus) -> None:
    _store[_key(exchange, symbol)] = (time.time(), status)
