"""Gate.io D/W — GET /wallet/currency_chains?currency=SYMBOL."""

from __future__ import annotations

import time
from typing import Any

import structlog

from crypto_bot.clients.dw.base import DepositWithdrawalClient
from crypto_bot.clients.dw.parse import pick_chain_field
from crypto_bot.models.dw import ExchangeDwResult, NetworkDwStatus

logger = structlog.get_logger(__name__)

_URL = "https://api.gateio.ws/api/v4/wallet/currency_chains"
_CACHE: dict[str, tuple[float, list]] = {}
_CACHE_TTL = 300


class GateDwClient(DepositWithdrawalClient):
    exchange_key = "gate"

    async def fetch_networks(self, symbol: str) -> ExchangeDwResult:
        sym = symbol.upper()
        self.log_request(sym, _URL, params={"currency": sym})

        try:
            hit = _CACHE.get(sym)
            if hit and time.time() - hit[0] < _CACHE_TTL:
                chains = hit[1]
            else:
                data = await self._http.get_json(_URL, params={"currency": sym}, timeout=8)
                chains = data if isinstance(data, list) else []
                _CACHE[sym] = (time.time(), chains)

            rows: list[NetworkDwStatus | None] = []
            for ch in chains:
                if not isinstance(ch, dict):
                    continue
                dep = None
                wdr = None
                if "is_deposit_disabled" in ch:
                    dep = not bool(ch.get("is_deposit_disabled"))
                if "is_withdraw_disabled" in ch:
                    wdr = not bool(ch.get("is_withdraw_disabled"))
                chain = pick_chain_field(
                    ch,
                    coin=sym,
                    prefer=("chain", "name_en", "name_cn"),
                ) or str(ch.get("chain") or ch.get("name_en") or "")
                rows.append(
                    self.row(
                        chain,
                        coin=sym,
                        deposit=dep,
                        withdraw=wdr,
                        min_deposit=ch.get("min_deposit_amount"),
                        min_withdraw=ch.get("min_withdraw_amount"),
                        withdraw_fee=ch.get("withdraw_fee") or ch.get("withdraw_txfee"),
                        api_hint=str(ch.get("chain") or "")[:40],
                    )
                )
            return self._result(sym, rows)
        except Exception as exc:
            logger.warning("gate_dw_error", symbol=sym, error=str(exc)[:120])
            return self._result(sym, [])
