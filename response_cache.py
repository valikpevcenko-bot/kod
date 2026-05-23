"""
Кэш готовых ответов — повторный /get за ~0.05 сек.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Optional

from models import ContractInfo, ExchangeSnapshot

# Секунды: свежий ответ отдаём мгновенно
REPORT_TTL = 25


@dataclass
class CachedReport:
    text: str
    snapshots: list[ExchangeSnapshot]
    contracts: list[ContractInfo]
    ts: float
    # False = только цены; True = контракты + D/W подтянуты
    complete: bool = False


_store: dict[str, CachedReport] = {}


def _key(base: str, quote: str) -> str:
    return f"{base.upper()}:{quote.upper()}"


def get(base: str, quote: str) -> Optional[CachedReport]:
    item = _store.get(_key(base, quote))
    if not item:
        return None
    if time.time() - item.ts > REPORT_TTL:
        return None
    return item


def set(
    base: str,
    quote: str,
    text: str,
    snapshots: list[ExchangeSnapshot],
    contracts: list[ContractInfo],
    *,
    complete: bool = False,
) -> None:
    _store[_key(base, quote)] = CachedReport(
        text=text,
        snapshots=snapshots,
        contracts=contracts,
        ts=time.time(),
        complete=complete,
    )
