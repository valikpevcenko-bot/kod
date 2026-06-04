"""MEXC D/W — capital/config/getall filtered by coin (each networkList entry = one chain)."""

from __future__ import annotations

import time
from typing import Any

import structlog

from crypto_bot.clients.dw.base import DepositWithdrawalClient
from crypto_bot.clients.dw.parse import pick_chain_field
from crypto_bot.config.settings import get_settings
from crypto_bot.core.exchange_sign import mexc_sign
from crypto_bot.models.dw import ExchangeDwResult, NetworkDwStatus

logger = structlog.get_logger(__name__)

_PUBLIC_URL = "https://api.mexc.com/api/v3/capital/config/getall"
_CACHE: tuple[float, list[dict[str, Any]]] | None = None
_CACHE_TTL = 480


class MexcDwClient(DepositWithdrawalClient):
    exchange_key = "mexc"

    async def _load_all(self) -> list[dict[str, Any]]:
        global _CACHE
        now = time.time()
        if _CACHE and now - _CACHE[0] < _CACHE_TTL:
            return _CACHE[1]

        settings = get_settings()
        creds = settings.credentials_for("mexc")
        self.log_request("ALL", _PUBLIC_URL, signed=bool(creds))
        data: Any = None
        if creds:
            ts = str(int(time.time() * 1000))
            query = f"timestamp={ts}"
            sig = mexc_sign(creds["secret"], query)
            data = await self._http.get_json(
                _PUBLIC_URL,
                params={"timestamp": ts, "signature": sig},
                headers={"X-MEXC-APIKEY": creds["apiKey"]},
                timeout=12,
            )
        if data is None:
            data = await self._http.get_json(_PUBLIC_URL, timeout=25)

        coins = data if isinstance(data, list) else []
        _CACHE = (time.time(), coins)
        return coins

    async def fetch_networks(self, symbol: str) -> ExchangeDwResult:
        sym = symbol.upper()
        self.log_request(sym, _PUBLIC_URL, filter_coin=sym)

        try:
            for item in await self._load_all():
                if str(item.get("coin", "")).upper() != sym:
                    continue
                rows: list[NetworkDwStatus | None] = []
                for net in item.get("networkList") or []:
                    if not isinstance(net, dict):
                        continue
                    chain = pick_chain_field(
                        net,
                        coin=sym,
                        prefer=("network", "netWork"),
                    )
                    rows.append(
                        self.row(
                            chain,
                            coin=sym,
                            deposit=net.get("depositEnable"),
                            withdraw=net.get("withdrawEnable"),
                            min_deposit=net.get("depositMin") or net.get("minDeposit"),
                            min_withdraw=net.get("withdrawMin"),
                            withdraw_fee=net.get("withdrawFee") or net.get("withdrawTips"),
                            api_hint=str(net.get("network") or net.get("netWork") or "")[:40],
                        )
                    )
                return self._result(sym, rows)
        except Exception as exc:
            logger.warning("mexc_dw_error", coin=sym, error=str(exc)[:120])
        return self._result(sym, [])
