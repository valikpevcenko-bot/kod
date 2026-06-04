"""OKX D/W — GET /api/v5/asset/currencies?ccy=SYMBOL (one row per chain)."""

from __future__ import annotations

import structlog

from crypto_bot.clients.dw.base import DepositWithdrawalClient
from crypto_bot.clients.dw.ccxt_fallback import fetch_ccxt_networks
from crypto_bot.clients.dw.parse import pick_chain_field
from crypto_bot.clients.exchanges.okx_rest import fetch_okx_currencies
from crypto_bot.config.settings import get_settings
from crypto_bot.core import guards
from crypto_bot.models.dw import ExchangeDwResult, NetworkDwStatus

logger = structlog.get_logger(__name__)

_URL = "https://www.okx.com/api/v5/asset/currencies"


def _okx_chain_part(chain_field: str, coin: str) -> str:
    """OKX chain is often 'MIRA-Solana' — use only the network segment."""
    text = str(chain_field or "").strip()
    if not text:
        return ""
    if "-" in text:
        left, right = text.split("-", 1)
        if left.upper() == coin.upper() and right.strip():
            return right.strip()
    return text


class OkxDwClient(DepositWithdrawalClient):
    exchange_key = "okx"

    async def fetch_networks(self, symbol: str) -> ExchangeDwResult:
        sym = symbol.upper()
        settings = get_settings()
        if not settings.has_auth("okx"):
            return self._result(sym, [], note="API key required")

        self.log_request(sym, _URL, params={"ccy": sym})

        try:
            data = await fetch_okx_currencies(self._http, settings, sym)
            if not data:
                logger.warning("okx_dw_empty", coin=sym)
                fallback = await fetch_ccxt_networks("okx", sym)
                return self._result(sym, fallback)

            rows: list[NetworkDwStatus | None] = []
            for item in data.get("data") or []:
                if not isinstance(item, dict):
                    continue
                if str(item.get("ccy", sym)).upper() != sym:
                    continue
                chain_field = _okx_chain_part(str(item.get("chain") or ""), sym)
                rows.append(
                    self.row(
                        pick_chain_field(
                            {"chain": chain_field},
                            coin=sym,
                            prefer=("chain",),
                        )
                        or chain_field,
                        coin=sym,
                        deposit=guards.dw_flag(item.get("canDep")),
                        withdraw=guards.dw_flag(item.get("canWd")),
                        min_deposit=item.get("minDep"),
                        min_withdraw=item.get("minWd"),
                        withdraw_fee=item.get("fee") or item.get("minFee"),
                        api_hint=chain_field[:60],
                    )
                )
            if any(r for r in rows if r):
                return self._result(sym, rows)
            fallback = await fetch_ccxt_networks("okx", sym)
            return self._result(sym, fallback)
        except Exception as exc:
            logger.warning("okx_dw_error", coin=sym, error=str(exc)[:120])
            fallback = await fetch_ccxt_networks("okx", sym)
            return self._result(sym, fallback)
