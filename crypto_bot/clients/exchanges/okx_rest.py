"""OKX signed REST (asset/currencies for D/W)."""

from __future__ import annotations

from typing import Any

import structlog

from crypto_bot.config.settings import Settings
from crypto_bot.core import guards
from crypto_bot.core.exchange_sign import okx_sign, okx_timestamp_ms
from crypto_bot.core.http import HttpClient

logger = structlog.get_logger(__name__)

_OKX_BASE = "https://www.okx.com"


async def fetch_okx_currencies(
    http: HttpClient,
    settings: Settings,
    symbol: str,
) -> dict[str, Any] | None:
    """GET /api/v5/asset/currencies?ccy=SYMBOL (requires API key + IP whitelist)."""
    creds = settings.credentials_for("okx")
    if not creds:
        return None
    ccy = symbol.upper()
    request_path = f"/api/v5/asset/currencies?ccy={ccy}"
    timestamp = okx_timestamp_ms()
    sign = okx_sign(creds["secret"], timestamp, "GET", request_path)
    headers = {
        "OK-ACCESS-KEY": creds["apiKey"],
        "OK-ACCESS-SIGN": sign,
        "OK-ACCESS-TIMESTAMP": timestamp,
        "OK-ACCESS-PASSPHRASE": creds.get("password", ""),
        "Content-Type": "application/json",
    }
    data = await http.get_json(f"{_OKX_BASE}{request_path}", headers=headers, timeout=8)
    if not isinstance(data, dict):
        return None
    code = str(data.get("code") or "")
    if not guards.okx_ok(code):
        msg = str(data.get("msg") or "")
        if code == "50110" or "whitelist" in msg.lower():
            logger.warning(
                "okx_ip_whitelist",
                hint="Add server IP to OKX API key whitelist (API → IP whitelist)",
            )
        else:
            logger.debug("okx_currencies_fail", code=code, msg=msg[:120])
        return None
    return data
