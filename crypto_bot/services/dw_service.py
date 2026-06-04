"""Deposit/withdrawal orchestration with TTL cache."""

from __future__ import annotations

import asyncio
import time

import structlog

from crypto_bot.clients.dw.registry import DW_EXCHANGE_KEYS, build_dw_clients
from crypto_bot.config.settings import get_settings
from crypto_bot.core.http import get_http
from crypto_bot.domain.exchanges import AUTH_WALLET_TIMEOUT, WALLET_CALL_TIMEOUT
from crypto_bot.models.market import WalletStatus

logger = structlog.get_logger(__name__)

_cache: dict[str, tuple[float, WalletStatus]] = {}
_AUTH_KEYS = frozenset({"bybit", "okx", "bingx"})
_MEXC_TIMEOUT = 25.0


def cache_get(exchange_key: str, symbol: str) -> WalletStatus | None:
    hit = _cache.get(f"{exchange_key}:{symbol.upper()}")
    if hit and time.time() - hit[0] < get_settings().dw_cache_ttl:
        return hit[1]
    return None


def cache_set(exchange_key: str, symbol: str, wallet: WalletStatus) -> None:
    _cache[f"{exchange_key}:{symbol.upper()}"] = (time.time(), wallet)


def has_rows(wallet: WalletStatus | None) -> bool:
    if not wallet:
        return False
    return any(n.deposit is not None or n.withdraw is not None for n in wallet.networks)


def _trustworthy(wallet: WalletStatus) -> bool:
    """Cache only when at least one network has an explicit API flag."""
    return any(
        n.deposit is not None or n.withdraw is not None for n in wallet.networks
    )


class DwService:
    """Fetches D/W via exchange-specific clients; caches 5–10 minutes."""

    def __init__(self) -> None:
        self._http = get_http()
        self._clients = build_dw_clients(self._http)

    async def fetch(self, exchange_key: str, symbol: str) -> WalletStatus:
        if exchange_key not in DW_EXCHANGE_KEYS:
            return WalletStatus()

        hit = cache_get(exchange_key, symbol)
        if hit is not None:
            return hit

        client = self._clients.get(exchange_key)
        if not client:
            return WalletStatus()

        if exchange_key in _AUTH_KEYS:
            timeout = AUTH_WALLET_TIMEOUT
        elif exchange_key == "mexc":
            timeout = _MEXC_TIMEOUT
        else:
            timeout = WALLET_CALL_TIMEOUT
        try:
            result = await asyncio.wait_for(client.fetch_networks(symbol), timeout=timeout)
            wallet = result.to_wallet_status()
            if has_rows(wallet) and _trustworthy(wallet):
                cache_set(exchange_key, symbol, wallet)
            return wallet
        except asyncio.TimeoutError:
            logger.warning("dw_timeout", exchange=exchange_key, symbol=symbol)
        except Exception as exc:
            logger.warning("dw_fetch_error", exchange=exchange_key, error=str(exc)[:120])
        return WalletStatus()

    async def fetch_bounded(self, exchange_key: str, symbol: str) -> WalletStatus:
        return await self.fetch(exchange_key, symbol)

    async def prefetch_mexc_capital(self) -> None:
        client = self._clients.get("mexc")
        if client is None:
            return
        await client._load_all()

    async def prefetch_many(
        self,
        symbol: str,
        exchange_keys: list[str],
    ) -> dict[str, WalletStatus]:
        keys = [k for k in exchange_keys if k in DW_EXCHANGE_KEYS]
        results = await asyncio.gather(
            *[self.fetch_bounded(k, symbol) for k in keys],
            return_exceptions=True,
        )
        out: dict[str, WalletStatus] = {}
        for key, result in zip(keys, results):
            if isinstance(result, WalletStatus):
                out[key] = result
            else:
                out[key] = WalletStatus()
        return out

    async def close(self) -> None:
        return
