"""CCXT fallback when signed REST fails (same API keys)."""

from __future__ import annotations

import asyncio
from typing import Any

import ccxt.async_support as ccxt
import structlog

from crypto_bot.clients.dw.parse import build_network_row
from crypto_bot.config.settings import get_settings
from crypto_bot.models.dw import NetworkDwStatus

logger = structlog.get_logger(__name__)


async def fetch_ccxt_networks(exchange_id: str, symbol: str) -> list[NetworkDwStatus]:
    sym = symbol.upper()
    settings = get_settings()
    creds = settings.credentials_for(exchange_id)
    if not creds:
        return []

    extra = {"defaultType": "spot"} if exchange_id == "okx" else None
    klass = getattr(ccxt, exchange_id)
    params: dict[str, Any] = {"enableRateLimit": True, "timeout": 20000, **creds}
    if extra:
        params.setdefault("options", {}).update(extra)

    ex = klass(params)
    try:
        currencies = await asyncio.wait_for(ex.fetch_currencies(), timeout=20)
        cur = currencies.get(sym) or currencies.get(symbol) or {}
        rows: list[NetworkDwStatus] = []
        for net_id, net in (cur.get("networks") or {}).items():
            if not isinstance(net, dict):
                continue
            dep, wdr = net.get("deposit"), net.get("withdraw")
            if dep is None and wdr is None:
                continue
            row = build_network_row(
                str(net.get("network") or net_id),
                coin=sym,
                exchange=exchange_id,
                deposit=bool(dep) if dep is not None else None,
                withdraw=bool(wdr) if wdr is not None else None,
            )
            if row:
                rows.append(row)
        if not rows and (cur.get("deposit") is not None or cur.get("withdraw") is not None):
            row = build_network_row(
                str(cur.get("name") or ""),
                coin=sym,
                exchange=exchange_id,
                deposit=cur.get("deposit"),
                withdraw=cur.get("withdraw"),
            )
            if row:
                rows.append(row)
        return rows
    except Exception as exc:
        logger.debug("ccxt_dw_fallback", exchange=exchange_id, error=str(exc)[:100])
        return []
    finally:
        await ex.close()
