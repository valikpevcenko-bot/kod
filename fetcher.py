"""Быстрая загрузка: REST-цены + кэш D/W и контрактов."""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Optional

from contracts import fetch_contracts, get_cached as contracts_cached
from fast_prices import FAST
from links import futures_url, spot_url
from models import ContractInfo, ExchangeSnapshot, MarketTicker
from wallet import enrich_wallet, fetch_wallet
from wallet_cache import get as wallet_cached, set as wallet_cache_set

logger = logging.getLogger(__name__)

# Лимит на весь /get (сек) — укладываемся ~1 с
FETCH_BUDGET = 0.95

EXCHANGE_DEFS: list[dict[str, Any]] = [
    {"key": "binance", "name": "Binance", "futures_only": False},
    {"key": "bybit", "name": "Bybit", "futures_only": False},
    {"key": "gate", "name": "Gate.io", "futures_only": False},
    {"key": "mexc", "name": "MEXC", "futures_only": False},
    {"key": "bitget", "name": "Bitget", "futures_only": False},
    {"key": "okx", "name": "OKX", "futures_only": False},
    {"key": "kucoin", "name": "KuCoin", "futures_only": False},
    {"key": "bingx", "name": "BingX", "futures_only": False},
    {"key": "htx", "name": "HTX", "futures_only": False},
    {"key": "aster", "name": "AsterDex", "futures_only": True},
    {"key": "hyperliquid", "name": "Hyperliquid", "futures_only": True},
]


async def _fetch_one_prices(defn: dict[str, Any], base: str, quote: str) -> ExchangeSnapshot:
    key = defn["key"]
    snap = ExchangeSnapshot(
        key=key,
        name=defn["name"],
        futures_only=bool(defn.get("futures_only")),
    )
    fetcher = FAST.get(key)
    if not fetcher:
        return snap
    try:
        spot_p, fut_p = await fetcher(base, quote)
    except Exception as exc:  # noqa: BLE001
        logger.debug("fast price %s: %s", key, exc)
        return snap

    if spot_p is not None:
        snap.spot = MarketTicker(spot_p, spot_url(key, base, quote))
    if fut_p is not None:
        snap.futures = MarketTicker(fut_p, futures_url(key, base, quote))
    return snap


def _attach_wallets(
    snapshots: list[ExchangeSnapshot],
    base: str,
    fresh_wallets: Optional[dict[str, Any]] = None,
) -> None:
    for snap in snapshots:
        if snap.futures_only:
            continue
        w = None
        if fresh_wallets:
            w = fresh_wallets.get(snap.key)
        if w is None:
            w = wallet_cached(snap.key, base)
        snap.wallet = w


async def fetch_all_fast(
    base: str,
    quote: str,
) -> tuple[list[ExchangeSnapshot], list[ContractInfo]]:
    """
    Укладывается в ~1 с: только REST-цены + кэш контрактов/D/W.
    """
    contracts = contracts_cached(base)

    try:
        prices_raw = await asyncio.wait_for(
            asyncio.gather(
                *[_fetch_one_prices(d, base, quote) for d in EXCHANGE_DEFS],
                return_exceptions=True,
            ),
            timeout=FETCH_BUDGET,
        )
    except asyncio.TimeoutError:
        logger.warning("prices timeout %s", base)
        prices_raw = []

    out: list[ExchangeSnapshot] = []
    for item in prices_raw:
        if isinstance(item, ExchangeSnapshot) and item.has_data:
            out.append(item)

    _attach_wallets(out, base)
    return out, contracts


async def fetch_all_full(
    base: str,
    quote: str,
    snapshots: list[ExchangeSnapshot],
    contracts: list[ContractInfo],
) -> tuple[list[ExchangeSnapshot], list[ContractInfo]]:
    """Фоновое обновление: контракты и D/W параллельно, D/W с учётом сетей контрактов."""
    listed = [s.key for s in snapshots]
    spot_defns = [d for d in EXCHANGE_DEFS if not d.get("futures_only")]

    full_contracts, wallets_raw = await asyncio.gather(
        fetch_contracts(base, listed_on=listed or None),
        asyncio.gather(
            *[fetch_wallet(d["key"], base, None) for d in spot_defns],
            return_exceptions=True,
        ),
    )
    if not isinstance(full_contracts, list):
        full_contracts = contracts
    if not full_contracts:
        full_contracts = contracts

    contract_networks = [c.network for c in full_contracts]

    fresh: dict[str, Any] = {}
    for defn, w in zip(spot_defns, wallets_raw):
        if isinstance(w, Exception):
            logger.debug("wallet %s %s: %s", defn["key"], base, w)
            continue
        w = enrich_wallet(w, base, contract_networks)
        fresh[defn["key"]] = w
        wallet_cache_set(defn["key"], base, w)

    _attach_wallets(snapshots, base, fresh)
    return snapshots, full_contracts


# Совместимость
async def fetch_all(base: str, quote: str) -> tuple[list[ExchangeSnapshot], list]:
    return await fetch_all_fast(base, quote)
