"""Binance D/W — getNetworkCoinAll filtered by coin (networkList = chains)."""

from __future__ import annotations

import time
from typing import Any

import structlog

from crypto_bot.clients.dw.base import DepositWithdrawalClient
from crypto_bot.clients.dw.parse import pick_chain_field
from crypto_bot.models.dw import NetworkDwStatus

logger = structlog.get_logger(__name__)

_BAPI_URL = "https://www.binance.com/bapi/capital/v1/public/capital/getNetworkCoinAll"
_CACHE: tuple[float, list[dict[str, Any]]] | None = None
_CACHE_TTL = 480


class BinanceDwClient(DepositWithdrawalClient):
    exchange_key = "binance"

    async def _all_coins(self) -> list[dict[str, Any]]:
        global _CACHE
        now = time.time()
        if _CACHE and now - _CACHE[0] < _CACHE_TTL:
            return _CACHE[1]
        self.log_request("ALL", _BAPI_URL)
        data = await self._http.get_json(_BAPI_URL, timeout=10)
        coins = (data or {}).get("data") or [] if isinstance(data, dict) else []
        _CACHE = (time.time(), coins if isinstance(coins, list) else [])
        return _CACHE[1]

    async def fetch_networks(self, symbol: str) -> ExchangeDwResult:
        sym = symbol.upper()
        self.log_request(sym, _BAPI_URL, filter_coin=sym)

        try:
            for item in await self._all_coins():
                if str(item.get("coin", "")).upper() != sym:
                    continue
                rows: list[NetworkDwStatus | None] = []
                for net in item.get("networkList") or []:
                    if not isinstance(net, dict):
                        continue
                    chain = pick_chain_field(
                        net,
                        coin=sym,
                        prefer=("network", "name"),
                    )
                    if net.get("busy"):
                        dep_val, wdr_val = False, False
                    else:
                        dep_val = net.get("depositEnable")
                        wdr_val = net.get("withdrawEnable")
                    rows.append(
                        self.row(
                            chain,
                            coin=sym,
                            deposit=dep_val,
                            withdraw=wdr_val,
                            min_deposit=net.get("depositDust") or net.get("minConfirm"),
                            min_withdraw=net.get("withdrawMin"),
                            withdraw_fee=net.get("withdrawFee"),
                            api_hint=str(net.get("network") or "")[:40],
                        )
                    )
                return self._result(sym, rows)
        except Exception as exc:
            logger.warning("binance_dw_error", coin=sym, error=str(exc)[:120])
        return self._result(sym, [])
