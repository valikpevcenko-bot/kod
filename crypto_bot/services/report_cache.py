"""In-memory report cache for fast /get repeats."""

from __future__ import annotations

import time

from crypto_bot.config.settings import get_settings
from crypto_bot.models.market import CachedReport, ContractInfo, ExchangeSnapshot


class ReportCache:
    """TTL cache keyed by BASE:QUOTE."""

    def __init__(self) -> None:
        self._store: dict[str, tuple[float, CachedReport]] = {}

    def _key(self, base: str, quote: str) -> str:
        return f"{base.upper()}:{quote.upper()}"

    def peek(self, base: str, quote: str) -> tuple[CachedReport, bool] | None:
        hit = self._store.get(self._key(base, quote))
        if not hit:
            return None
        age = time.time() - hit[0]
        settings = get_settings()
        if age > settings.report_cache_stale_ttl:
            return None
        return hit[1], age > settings.report_cache_ttl

    def set(
        self,
        base: str,
        quote: str,
        text: str,
        snapshots: list[ExchangeSnapshot],
        contracts: list[ContractInfo],
        *,
        complete: bool,
    ) -> None:
        self._store[self._key(base, quote)] = (
            time.time(),
            CachedReport(
                text=text,
                snapshots=snapshots,
                contracts=contracts,
                complete=complete,
            ),
        )
