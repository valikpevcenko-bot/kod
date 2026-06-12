"""Kraken D/W — private DepositMethods/WithdrawMethods + public Assets fallback."""

from __future__ import annotations

import time
from typing import Any

import structlog

from crypto_bot.clients.dw.base import DepositWithdrawalClient
from crypto_bot.config.settings import get_settings
from crypto_bot.core.exchange_sign import kraken_private_headers
from crypto_bot.domain.network_registry import CANONICAL_NETWORKS
from crypto_bot.models.dw import ExchangeDwResult, NetworkDwStatus

logger = structlog.get_logger(__name__)

_BASE = "https://api.kraken.com"
_ASSETS_URL = f"{_BASE}/0/public/Assets"
_DEPOSIT_PATH = "/0/private/DepositMethods"
_WITHDRAW_PATH = "/0/private/WithdrawMethods"

_assets_cache: tuple[float, dict[str, Any]] | None = None
_ASSETS_TTL = 300
_nonce_seq = 0


def _kraken_asset_query(symbol: str) -> str:
    s = symbol.upper()
    return "XBT" if s == "BTC" else s


def _native_network(symbol: str) -> str | None:
    s = symbol.upper()
    if s in ("BTC", "XBT"):
        return "BTC"
    if s in CANONICAL_NETWORKS:
        return s
    return None


def _next_nonce() -> str:
    global _nonce_seq
    _nonce_seq += 1
    return str(int(time.time() * 1000) + _nonce_seq)


class KrakenDwClient(DepositWithdrawalClient):
    exchange_key = "kraken"

    async def _public_assets(self) -> dict[str, Any]:
        global _assets_cache
        now = time.time()
        if _assets_cache and now - _assets_cache[0] < _ASSETS_TTL:
            return _assets_cache[1]
        data = await self._http.get_json(_ASSETS_URL, timeout=10)
        assets: dict[str, Any] = {}
        if isinstance(data, dict) and not data.get("error"):
            assets = data.get("result") or {}
        _assets_cache = (now, assets)
        return assets

    async def _asset_status(self, symbol: str) -> str | None:
        query = _kraken_asset_query(symbol)
        for meta in (await self._public_assets()).values():
            if not isinstance(meta, dict):
                continue
            if str(meta.get("altname") or "").upper() == query:
                return str(meta.get("status") or "").lower() or None
        return None

    async def _private_form(self, path: str, params: dict[str, str]) -> tuple[list[dict[str, Any]], list[str]]:
        creds = get_settings().credentials_for("kraken")
        if not creds:
            return [], ["no_api_key"]
        data = {"nonce": _next_nonce(), **params}
        headers = kraken_private_headers(creds["apiKey"], creds["secret"], path, data)
        url = _BASE + path
        self.log_request(params.get("asset", ""), url, **params)

        payload = await self._http.post_form(url, data=data, headers=headers, timeout=12.0)
        if not isinstance(payload, dict):
            return [], ["bad_json"]
        errors = [str(e) for e in (payload.get("error") or [])]
        result = payload.get("result")
        if isinstance(result, list):
            return [r for r in result if isinstance(r, dict)], errors
        return [], errors

    @staticmethod
    def _chain_raw(sym: str, payload: dict[str, Any]) -> str:
        """Kraken: deposit uses method='BDX - BNB Chain', withdraw uses network='BNB Chain'."""
        network = str(payload.get("network") or "").strip()
        if network:
            return network
        method = str(payload.get("method") or "").strip()
        if " - " in method:
            return method.split(" - ", 1)[1].strip()
        return method

    def _row_from_method(
        self,
        sym: str,
        payload: dict[str, Any],
        *,
        deposit: bool | None = None,
        withdraw: bool | None = None,
    ) -> NetworkDwStatus | None:
        chain = self._chain_raw(sym, payload)
        if not chain:
            return None
        fee_obj = payload.get("fee")
        fee_val = None
        if isinstance(fee_obj, dict):
            fee_val = fee_obj.get("fee")
        elif fee_obj is not None:
            fee_val = fee_obj
        return self.row(
            chain,
            coin=sym,
            deposit=deposit,
            withdraw=withdraw,
            min_deposit=payload.get("minimum"),
            min_withdraw=payload.get("minimum"),
            withdraw_fee=fee_val,
            api_hint=str(payload.get("method") or payload.get("network") or "")[:60],
        )

    @staticmethod
    def _merge_row(
        prev: NetworkDwStatus | None,
        row: NetworkDwStatus,
    ) -> NetworkDwStatus:
        if prev is None:
            return row
        from crypto_bot.clients.dw.base import _merge_dw_flag

        return NetworkDwStatus(
            network=row.network,
            deposit=_merge_dw_flag(row.deposit, prev.deposit, strict=True),
            withdraw=_merge_dw_flag(row.withdraw, prev.withdraw, strict=True),
            min_deposit=row.min_deposit or prev.min_deposit,
            min_withdraw=row.min_withdraw or prev.min_withdraw,
            withdraw_fee=row.withdraw_fee or prev.withdraw_fee,
        )

    async def _fetch_private(self, sym: str, asset: str) -> list[NetworkDwStatus]:
        by_net: dict[str, NetworkDwStatus] = {}

        dep_rows, dep_err = await self._private_form(_DEPOSIT_PATH, {"asset": asset, "aclass": "currency"})
        for item in dep_rows:
            limited = bool(item.get("limit"))
            row = self._row_from_method(
                sym,
                item,
                deposit=False if limited else True,
                withdraw=None,
            )
            if row:
                by_net[row.network] = self._merge_row(by_net.get(row.network), row)

        wdr_rows, wdr_err = await self._private_form(_WITHDRAW_PATH, {"asset": asset, "aclass": "currency"})
        for item in wdr_rows:
            row = self._row_from_method(sym, item, deposit=None, withdraw=True)
            if row:
                by_net[row.network] = self._merge_row(by_net.get(row.network), row)

        errors = dep_err + wdr_err
        if by_net:
            return list(by_net.values())
        if any("Permission denied" in e for e in errors):
            logger.info("kraken_dw_permission_denied", symbol=sym)
        elif errors:
            logger.warning("kraken_dw_private_fail", symbol=sym, errors=errors[:3])
        return []

    async def _fetch_public_fallback(self, sym: str) -> list[NetworkDwStatus]:
        status = await self._asset_status(sym)
        if status != "enabled":
            return []
        native = _native_network(sym)
        if not native:
            return []
        row = self.row(native, coin=sym, deposit=True, withdraw=True, api_hint="public:enabled")
        return [row] if row else []

    async def fetch_networks(self, symbol: str) -> ExchangeDwResult:
        sym = symbol.upper()
        asset = _kraken_asset_query(sym)
        try:
            rows = await self._fetch_private(sym, asset)
            if rows:
                return self._result(sym, rows)
            fallback = await self._fetch_public_fallback(sym)
            if fallback:
                return self._result(
                    sym,
                    fallback,
                    note="public asset status (enable Funds permissions on API key for per-network fees)",
                )
            return self._result(sym, [])
        except Exception as exc:
            logger.warning("kraken_dw_error", symbol=sym, error=str(exc)[:120])
            fallback = await self._fetch_public_fallback(sym)
            return self._result(sym, fallback)
