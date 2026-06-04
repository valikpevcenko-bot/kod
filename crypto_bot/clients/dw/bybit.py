"""Bybit D/W — GET /v5/asset/coin/query-info?coin=SYMBOL (per token, per chain)."""

from __future__ import annotations

import structlog

from crypto_bot.clients.dw.base import DepositWithdrawalClient
from crypto_bot.clients.dw.ccxt_fallback import fetch_ccxt_networks
from crypto_bot.clients.dw.parse import pick_chain_field
from crypto_bot.config.settings import get_settings
from crypto_bot.core import guards
from crypto_bot.core.exchange_sign import bybit_headers
from crypto_bot.models.dw import ExchangeDwResult, NetworkDwStatus

logger = structlog.get_logger(__name__)

_URL = "https://api.bybit.com/v5/asset/coin/query-info"


class BybitDwClient(DepositWithdrawalClient):
    exchange_key = "bybit"

    async def fetch_networks(self, symbol: str) -> ExchangeDwResult:
        sym = symbol.upper()
        settings = get_settings()
        creds = settings.credentials_for("bybit")
        if not creds:
            return self._result(sym, [], note="API key required")

        self.log_request(sym, _URL, params={"coin": sym})

        try:
            headers = bybit_headers(creds["apiKey"], creds["secret"], {"coin": sym})
            data = await self._http.get_json(_URL, params={"coin": sym}, headers=headers, timeout=10)
            if not isinstance(data, dict) or not guards.ret_ok(data.get("retCode")):
                msg = str(data.get("retMsg") if isinstance(data, dict) else "")[:120]
                logger.warning("bybit_dw_fail", coin=sym, msg=msg)
                fallback = await fetch_ccxt_networks("bybit", sym)
                return self._result(sym, fallback)

            rows: list[NetworkDwStatus | None] = []
            raw_count = 0
            for item in (data.get("result") or {}).get("rows") or []:
                if str(item.get("coin", "")).upper() not in ("", sym):
                    continue
                for ch in item.get("chains") or []:
                    if not isinstance(ch, dict):
                        continue
                    raw_count += 1
                    chain = pick_chain_field(
                        ch,
                        coin=sym,
                        prefer=("chain", "chainType", "chainName", "network"),
                    ) or str(ch.get("chainType") or ch.get("chain") or "")
                    dep = guards.dw_flag(ch.get("chainDeposit"))
                    wdr = guards.dw_flag(ch.get("chainWithdraw"))
                    rows.append(
                        self.row(
                            chain,
                            coin=sym,
                            deposit=dep,
                            withdraw=wdr,
                            min_deposit=ch.get("depositMin"),
                            min_withdraw=ch.get("withdrawMin"),
                            withdraw_fee=ch.get("withdrawFee") or ch.get("fee"),
                            api_hint=str(ch)[:120],
                        )
                    )
            if any(r for r in rows if r):
                return self._result(sym, rows, note=None)
            fallback = await fetch_ccxt_networks("bybit", sym)
            return self._result(sym, fallback)
        except Exception as exc:
            logger.warning("bybit_dw_error", coin=sym, error=str(exc)[:120])
            fallback = await fetch_ccxt_networks("bybit", sym)
            return self._result(sym, fallback)
