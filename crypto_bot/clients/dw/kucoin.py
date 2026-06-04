"""KuCoin D/W — GET /api/v2/currencies/{coin}."""

from __future__ import annotations

import structlog

from crypto_bot.clients.dw.base import DepositWithdrawalClient
from crypto_bot.clients.dw.parse import pick_chain_field
from crypto_bot.core import guards
from crypto_bot.models.dw import ExchangeDwResult, NetworkDwStatus

logger = structlog.get_logger(__name__)


class KucoinDwClient(DepositWithdrawalClient):
    exchange_key = "kucoin"

    async def fetch_networks(self, symbol: str) -> ExchangeDwResult:
        sym = symbol.upper()
        url = f"https://api.kucoin.com/api/v2/currencies/{sym}"
        self.log_request(sym, url)

        try:
            data = await self._http.get_json(url, timeout=8)
            if not isinstance(data, dict) or not guards.kucoin_ok(data.get("code")):
                return self._result(sym, [])
            row = data.get("data") or {}
            rows: list[NetworkDwStatus | None] = []
            for ch in row.get("chains") or []:
                if not isinstance(ch, dict):
                    continue
                chain = pick_chain_field(
                    ch,
                    coin=sym,
                    prefer=("chainName", "chain"),
                ) or str(ch.get("chainName") or ch.get("chain") or "")
                rows.append(
                    self.row(
                        chain,
                        coin=sym,
                        deposit=ch.get("isDepositEnabled"),
                        withdraw=ch.get("isWithdrawEnabled"),
                        min_deposit=ch.get("depositMinSize") or ch.get("depositMin"),
                        min_withdraw=ch.get("withdrawalMinSize") or ch.get("withdrawMinSize"),
                        withdraw_fee=ch.get("withdrawalMinFee") or ch.get("withdrawFee"),
                        api_hint=str(ch.get("chainName") or ch.get("chain") or "")[:40],
                    )
                )
            return self._result(sym, rows)
        except Exception as exc:
            logger.warning("kucoin_dw_error", symbol=sym, error=str(exc)[:120])
            return self._result(sym, [])
