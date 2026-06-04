"""Backward-compatible aliases for D/W service."""

from __future__ import annotations

from crypto_bot.clients.dw.registry import DW_EXCHANGE_KEYS
from crypto_bot.services.dw_service import (
    DwService,
    cache_get as wallet_cache_get,
    cache_set as wallet_cache_set,
    has_rows as wallet_has_rows,
)

# All spot exchanges with D/W clients — fetched on enrich
FAST_WALLET_KEYS = DW_EXCHANGE_KEYS

__all__ = [
    "DwService",
    "WalletService",
    "FAST_WALLET_KEYS",
    "wallet_cache_get",
    "wallet_cache_set",
    "wallet_has_rows",
]

# Legacy name
WalletService = DwService
