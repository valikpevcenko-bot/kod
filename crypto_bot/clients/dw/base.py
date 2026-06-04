"""Deposit / withdrawal client interface."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

import structlog

from crypto_bot.clients.dw.parse import build_network_row
from crypto_bot.domain.network_labels import sort_network_rows
from crypto_bot.core.http import HttpClient
from crypto_bot.models.dw import ExchangeDwResult, NetworkDwStatus

logger = structlog.get_logger(__name__)


def _merge_dw_flag(
    new: bool | None,
    old: bool | None,
    *,
    strict: bool = False,
) -> bool | None:
    """Merge D/W flags; strict withdraw = enabled only if all sources say True."""
    if strict:
        if new is False or old is False:
            return False
        if new is True and old is True:
            return True
        return new if old is None else old
    if new is True or old is True:
        return True
    if new is False and old is False:
        return False
    return new if old is None else old


class DepositWithdrawalClient(ABC):
    """Fetch per-network deposit/withdrawal status for one coin."""

    exchange_key: str

    def __init__(self, http: HttpClient) -> None:
        self._http = http

    @abstractmethod
    async def fetch_networks(self, symbol: str) -> ExchangeDwResult:
        """Return all networks for symbol on this exchange."""

    def log_request(self, symbol: str, url: str, **extra: Any) -> None:
        logger.debug(
            "dw_api_request",
            exchange=self.exchange_key,
            coin=symbol.upper(),
            url=url,
            **{k: v for k, v in extra.items() if v is not None},
        )

    def log_response(
        self,
        symbol: str,
        networks: list[NetworkDwStatus],
        *,
        raw_count: int | None = None,
    ) -> None:
        logger.info(
            "dw_api_response",
            exchange=self.exchange_key,
            coin=symbol.upper(),
            raw_rows=raw_count,
            networks=[
                {
                    "net": n.network,
                    "dep": n.deposit,
                    "wdr": n.withdraw,
                }
                for n in networks
            ],
        )

    def _result(
        self,
        symbol: str,
        networks: list[NetworkDwStatus | None],
        note: str | None = None,
    ) -> ExchangeDwResult:
        sym = symbol.upper()
        deduped: dict[str, NetworkDwStatus] = {}
        for item in networks:
            if item is None:
                continue
            prev = deduped.get(item.network)
            if prev is None:
                deduped[item.network] = item
                continue
            deduped[item.network] = NetworkDwStatus(
                network=item.network,
                exchange_coin=item.exchange_coin or prev.exchange_coin,
                deposit=_merge_dw_flag(item.deposit, prev.deposit, strict=True),
                withdraw=_merge_dw_flag(item.withdraw, prev.withdraw, strict=True),
                min_deposit=item.min_deposit or prev.min_deposit,
                min_withdraw=item.min_withdraw or prev.min_withdraw,
                withdraw_fee=item.withdraw_fee or prev.withdraw_fee,
            )
        out = sort_network_rows(list(deduped.values()))
        self.log_response(sym, out)
        return ExchangeDwResult(
            exchange_key=self.exchange_key,
            symbol=sym,
            networks=out,
            note=note,
        )

    def row(
        self,
        chain_raw: str,
        *,
        coin: str,
        deposit: Any = None,
        withdraw: Any = None,
        min_deposit: Any = None,
        min_withdraw: Any = None,
        withdraw_fee: Any = None,
        api_hint: str = "",
        exchange_coin: str | None = None,
    ) -> NetworkDwStatus | None:
        return build_network_row(
            chain_raw,
            coin=coin,
            exchange=self.exchange_key,
            deposit=deposit,
            withdraw=withdraw,
            min_deposit=min_deposit,
            min_withdraw=min_withdraw,
            withdraw_fee=withdraw_fee,
            api_hint=api_hint,
            exchange_coin=exchange_coin,
        )


def _row(
    chain_raw: str,
    *,
    coin: str = "",
    exchange: str = "",
    deposit: Any = None,
    withdraw: Any = None,
    min_deposit: Any = None,
    min_withdraw: Any = None,
    withdraw_fee: Any = None,
) -> NetworkDwStatus | None:
    """Legacy helper for tests; prefer DepositWithdrawalClient.row()."""
    return build_network_row(
        chain_raw,
        coin=coin or "___",
        exchange=exchange,
        deposit=deposit,
        withdraw=withdraw,
        min_deposit=min_deposit,
        min_withdraw=min_withdraw,
        withdraw_fee=withdraw_fee,
    )
