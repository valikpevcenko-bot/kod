"""HTX (Huobi) D/W — reference/currencies (chain field, not displayName alone)."""

from __future__ import annotations

import structlog

from crypto_bot.clients.dw.base import DepositWithdrawalClient
from crypto_bot.clients.dw.parse import pick_chain_field
from crypto_bot.models.dw import ExchangeDwResult, NetworkDwStatus

logger = structlog.get_logger(__name__)

_URL = "https://api.htx.com/v2/reference/currencies"


class HtxDwClient(DepositWithdrawalClient):
    exchange_key = "htx"

    async def fetch_networks(self, symbol: str) -> ExchangeDwResult:
        sym = symbol.upper()
        self.log_request(sym, _URL, filter_currency=sym.lower())

        try:
            data = await self._http.get_json(_URL, timeout=10)
            if not isinstance(data, dict) or data.get("code") != 200:
                return self._result(sym, [])
            rows: list[NetworkDwStatus | None] = []
            for item in data.get("data") or []:
                if str(item.get("currency", "")).lower() != sym.lower():
                    continue
                for ch in item.get("chains") or []:
                    if not isinstance(ch, dict):
                        continue
                    dep = ch.get("depositStatus") == "allowed"
                    wdr = ch.get("withdrawStatus") == "allowed"
                    chain = pick_chain_field(
                        ch,
                        coin=sym,
                        prefer=("chain", "baseChain"),
                    )
                    rows.append(
                        self.row(
                            chain,
                            coin=sym,
                            deposit=dep if ch.get("depositStatus") else None,
                            withdraw=wdr if ch.get("withdrawStatus") else None,
                            min_deposit=ch.get("minDepositAmt"),
                            min_withdraw=ch.get("minWithdrawAmt"),
                            withdraw_fee=ch.get("transactFeeWithdraw"),
                            api_hint=str(ch.get("chain") or ch.get("baseChain") or "")[:40],
                        )
                    )
                return self._result(sym, rows)
        except Exception as exc:
            logger.warning("htx_dw_error", symbol=sym, error=str(exc)[:120])
        return self._result(sym, [])
