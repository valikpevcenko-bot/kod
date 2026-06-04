"""Shared D/W row parsing — one chain per row, strict network resolution."""

from __future__ import annotations

from typing import Any

import structlog

from crypto_bot.core import guards
from crypto_bot.domain.network_registry import resolve_network
from crypto_bot.models.dw import NetworkDwStatus

logger = structlog.get_logger(__name__)


def _fmt_amount(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text or text.lower() in ("0", "0.0", "0.00"):
        return None
    return text


def pick_chain_field(
    payload: dict[str, Any],
    *,
    coin: str,
    prefer: tuple[str, ...] = ("network", "chain", "chainType", "chainName", "netWork"),
) -> str:
    """
    Extract chain name from API object.
    Never use displayName / coin name as chain unless it resolves to a real network.
    """
    for key in prefer:
        val = str(payload.get(key) or "").strip()
        if val and resolve_network(val, coin=coin):
            return val
    for key in ("name",):
        val = str(payload.get(key) or "").strip()
        if val and resolve_network(val, coin=coin):
            return val
    return ""


def build_network_row(
    chain_raw: str,
    *,
    coin: str,
    exchange: str,
    deposit: Any = None,
    withdraw: Any = None,
    min_deposit: Any = None,
    min_withdraw: Any = None,
    withdraw_fee: Any = None,
    api_hint: str = "",
    exchange_coin: str | None = None,
) -> NetworkDwStatus | None:
    canonical = resolve_network(chain_raw, coin=coin, exchange=exchange)
    if not canonical:
        logger.debug(
            "dw_skip_unresolved_chain",
            exchange=exchange,
            coin=coin,
            raw=(chain_raw or "")[:60],
            hint=api_hint[:80],
        )
        return None

    dep = deposit if isinstance(deposit, bool) else guards.dw_flag(deposit)
    wdr = withdraw if isinstance(withdraw, bool) else guards.dw_flag(withdraw)

    if dep is None and wdr is None:
        logger.debug(
            "dw_skip_no_flags",
            exchange=exchange,
            coin=coin,
            canonical=canonical,
            hint=api_hint[:80],
        )
        return None

    logger.debug(
        "dw_chain_mapped",
        exchange=exchange,
        coin=coin,
        raw=(chain_raw or "")[:40],
        canonical=canonical,
        deposit=dep,
        withdraw=wdr,
    )

    return NetworkDwStatus(
        network=canonical,
        exchange_coin=(exchange_coin or "").strip().upper() or None,
        deposit=dep,
        withdraw=wdr,
        min_deposit=_fmt_amount(min_deposit),
        min_withdraw=_fmt_amount(min_withdraw),
        withdraw_fee=_fmt_amount(withdraw_fee),
    )
