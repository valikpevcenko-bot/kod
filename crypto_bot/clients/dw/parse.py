"""Shared D/W row parsing — one chain per row, strict network resolution."""

from __future__ import annotations

import re
from typing import Any

import structlog

from crypto_bot.core import guards
from crypto_bot.domain.network_registry import is_token_ticker, resolve_network
from crypto_bot.models.dw import NetworkDwStatus

logger = structlog.get_logger(__name__)

_CHAIN_FIELD_KEYS: tuple[str, ...] = (
    "network",
    "chain",
    "chainType",
    "chainName",
    "netWork",
    "name",
    "name_en",
    "name_cn",
    "displayName",
)

_NATIVE_SUFFIXES: tuple[str, ...] = ("NETWORK", "NET", "CHAIN", "MAINNET", "NATIVE")
_COMPACT_RE = re.compile(r"[^A-Z0-9]")


def _fmt_amount(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text or text.lower() in ("0", "0.0", "0.00"):
        return None
    return text


def _compact_label(text: str) -> str:
    return _COMPACT_RE.sub("", text.upper())


def _strip_native_suffix(compact: str) -> str:
    for suffix in sorted(_NATIVE_SUFFIXES, key=len, reverse=True):
        if compact.endswith(suffix) and len(compact) > len(suffix):
            return compact[: -len(suffix)]
    return compact


def _has_dw_flags(
    payload: dict[str, Any] | None,
    *,
    deposit: Any = None,
    withdraw: Any = None,
) -> bool:
    if deposit is not None or withdraw is not None:
        return True
    if not payload:
        return False
    for key in (
        "isDepositEnabled",
        "isWithdrawEnabled",
        "deposit",
        "withdraw",
        "depositEnable",
        "withdrawEnable",
        "chainDeposit",
        "chainWithdraw",
        "is_deposit_disabled",
        "is_withdraw_disabled",
        "rechargeable",
        "withdrawable",
        "is_disabled",
    ):
        if key in payload and payload[key] is not None:
            return True
    return False


def resolve_dw_network(
    raw: str,
    *,
    coin: str,
    exchange: str = "",
    deposit: Any = None,
    withdraw: Any = None,
) -> str | None:
    """
    Map exchange chain label to canonical network for D/W rows.
    Stricter than contracts; accepts native-L1 labels when API sent D/W flags.
    """
    text = str(raw or "").strip()
    if not text:
        return None

    dep = deposit if isinstance(deposit, bool) else guards.dw_flag(deposit)
    wdr = withdraw if isinstance(withdraw, bool) else guards.dw_flag(withdraw)
    has_flags = dep is not None or wdr is not None

    coin_up = coin.strip().upper()

    canonical = resolve_network(text, coin=coin, exchange=exchange)
    if canonical:
        if _strip_native_suffix(_compact_label(canonical)) == coin_up:
            return coin_up
        return canonical

    if not has_flags:
        return None

    compact = _strip_native_suffix(_compact_label(text))

    if compact == coin_up:
        return coin_up

    if not is_token_ticker(text, coin):
        label = text.upper().replace(" ", "")[:20]
        if label and "/" not in text:
            return label

    if is_token_ticker(text, coin) and compact == coin_up:
        return coin_up

    return None


def pick_chain_field(
    payload: dict[str, Any],
    *,
    coin: str,
    prefer: tuple[str, ...] = ("network", "chain", "chainType", "chainName", "netWork"),
) -> str:
    """Extract chain name; with D/W flags accept exchange-native labels too."""
    keys = tuple(dict.fromkeys((*prefer, *_CHAIN_FIELD_KEYS)))
    for key in keys:
        val = str(payload.get(key) or "").strip()
        if val and resolve_network(val, coin=coin):
            return val
    if _has_dw_flags(payload):
        for key in keys:
            val = str(payload.get(key) or "").strip()
            if val:
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
    dep = deposit if isinstance(deposit, bool) else guards.dw_flag(deposit)
    wdr = withdraw if isinstance(withdraw, bool) else guards.dw_flag(withdraw)

    canonical = resolve_dw_network(
        chain_raw,
        coin=coin,
        exchange=exchange,
        deposit=dep,
        withdraw=wdr,
    )
    if not canonical:
        logger.debug(
            "dw_skip_unresolved_chain",
            exchange=exchange,
            coin=coin,
            raw=(chain_raw or "")[:60],
            hint=api_hint[:80],
        )
        return None

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
