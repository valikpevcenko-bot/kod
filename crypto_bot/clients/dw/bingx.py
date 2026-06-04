"""BingX D/W — capital/config/getall; merge MIRANETWORK (BSC) + MIRA (SOL)."""

from __future__ import annotations

import time
from typing import Any

import structlog

from crypto_bot.clients.dw.base import DepositWithdrawalClient, _merge_dw_flag
from crypto_bot.clients.dw.parse import pick_chain_field
from crypto_bot.config.settings import get_settings
from crypto_bot.core.exchange_sign import bingx_timestamp_params
from crypto_bot.models.dw import NetworkDwStatus

logger = structlog.get_logger(__name__)

_URL = "https://open-api.bingx.com/openApi/wallets/v1/capital/config/getall"
_CACHE: tuple[float, list[dict[str, Any]]] | None = None
_CACHE_TTL = 480


class BingxDwClient(DepositWithdrawalClient):
    exchange_key = "bingx"

    async def _all_coins(self) -> list[dict[str, Any]]:
        global _CACHE
        settings = get_settings()
        creds = settings.credentials_for("bingx")
        if not creds:
            return []

        now = time.time()
        if _CACHE and now - _CACHE[0] < _CACHE_TTL:
            return _CACHE[1]

        self.log_request("ALL", _URL)
        params = bingx_timestamp_params(creds["apiKey"], creds["secret"], {})
        data = await self._http.get_json(
            _URL,
            params=params,
            headers={"X-BX-APIKEY": creds["apiKey"]},
            timeout=12,
        )
        coins: list[dict[str, Any]] = []
        if isinstance(data, dict):
            payload = data.get("data")
            if isinstance(payload, list):
                coins = payload
        _CACHE = (time.time(), coins)
        return coins

    def _rows_from_item(self, item: dict[str, Any], sym: str) -> list[NetworkDwStatus | None]:
        rows: list[NetworkDwStatus | None] = []
        for net in item.get("networkList") or []:
            if not isinstance(net, dict):
                continue
            chain = pick_chain_field(
                net,
                coin=sym,
                prefer=("network", "name"),
            ) or str(net.get("network") or "")
            api_coin = str(item.get("coin") or "").upper()
            rows.append(
                self.row(
                    chain,
                    coin=sym,
                    exchange_coin=api_coin,
                    deposit=net.get("depositEnable"),
                    withdraw=net.get("withdrawEnable"),
                    min_deposit=net.get("depositMin"),
                    min_withdraw=net.get("withdrawMin"),
                    withdraw_fee=net.get("withdrawFee"),
                    api_hint=f"coin={api_coin} network={net.get('network')}",
                )
            )
        return rows

    def _coin_entries(self, coins: list[dict[str, Any]], symbol: str) -> list[dict[str, Any]]:
        sym = symbol.upper()
        ranked: list[tuple[int, dict[str, Any]]] = []
        seen: set[str] = set()
        for item in coins:
            coin = str(item.get("coin", "")).upper()
            if not coin or coin in seen:
                continue
            priority: int | None = None
            if coin == f"{sym}NETWORK" or coin == f"{sym}NET":
                priority = 0
            elif coin == sym:
                priority = 2
            else:
                for net in item.get("networkList") or []:
                    if str(net.get("displayName") or "").upper() == sym:
                        priority = 1
                        break
            if priority is None:
                continue
            seen.add(coin)
            ranked.append((priority, item))
        ranked.sort(key=lambda x: x[0])
        return [item for _, item in ranked]

    async def fetch_networks(self, symbol: str) -> ExchangeDwResult:
        sym = symbol.upper()
        if not get_settings().has_auth("bingx"):
            return self._result(sym, [], note="API key required")

        self.log_request(sym, _URL, filter_coin=sym)

        try:
            coins = await self._all_coins()
            entries = self._coin_entries(coins, sym)
            merged: dict[str, NetworkDwStatus] = {}
            for item in entries:
                for row in self._rows_from_item(item, sym):
                    if row is None:
                        continue
                    prev = merged.get(row.network)
                    if prev is None:
                        merged[row.network] = row
                    else:
                        merged[row.network] = NetworkDwStatus(
                            network=row.network,
                            exchange_coin=row.exchange_coin or prev.exchange_coin,
                            deposit=_merge_dw_flag(row.deposit, prev.deposit, strict=True),
                            withdraw=_merge_dw_flag(row.withdraw, prev.withdraw, strict=True),
                            min_deposit=row.min_deposit or prev.min_deposit,
                            min_withdraw=row.min_withdraw or prev.min_withdraw,
                            withdraw_fee=row.withdraw_fee or prev.withdraw_fee,
                        )
            return self._result(sym, list(merged.values()))
        except Exception as exc:
            logger.warning("bingx_dw_error", coin=sym, error=str(exc)[:120])
        return self._result(sym, [])
