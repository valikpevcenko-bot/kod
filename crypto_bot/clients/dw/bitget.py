"""Bitget D/W — GET /spot/public/coins?coin=SYMBOL."""

from __future__ import annotations

import structlog

from crypto_bot.clients.dw.base import DepositWithdrawalClient
from crypto_bot.clients.dw.parse import pick_chain_field
from crypto_bot.models.dw import ExchangeDwResult, NetworkDwStatus

logger = structlog.get_logger(__name__)

_URL = "https://api.bitget.com/api/v2/spot/public/coins"


class BitgetDwClient(DepositWithdrawalClient):
    exchange_key = "bitget"

    async def fetch_networks(self, symbol: str) -> ExchangeDwResult:
        sym = symbol.upper()
        self.log_request(sym, _URL, params={"coin": sym})

        try:
            data = await self._http.get_json(_URL, params={"coin": sym}, timeout=8)
            if not isinstance(data, dict):
                return self._result(sym, [])
            rows: list[NetworkDwStatus | None] = []
            for item in data.get("data") or []:
                for ch in item.get("chains") or []:
                    if not isinstance(ch, dict):
                        continue
                    chain = pick_chain_field(ch, coin=sym, prefer=("chain",))
                    rows.append(
                        self.row(
                            chain,
                            coin=sym,
                            deposit=str(ch.get("rechargeable", "")).lower() == "true",
                            withdraw=str(ch.get("withdrawable", "")).lower() == "true",
                            min_deposit=ch.get("minDepositAmount") or ch.get("depositMin"),
                            min_withdraw=ch.get("minWithdrawAmount") or ch.get("withdrawMin"),
                            withdraw_fee=ch.get("withdrawFee"),
                            api_hint=str(ch.get("chain") or "")[:40],
                        )
                    )
            return self._result(sym, rows)
        except Exception as exc:
            logger.warning("bitget_dw_error", symbol=sym, error=str(exc)[:120])
            return self._result(sym, [])
