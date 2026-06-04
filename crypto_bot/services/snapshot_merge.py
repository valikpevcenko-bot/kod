"""Merge exchange snapshots without dropping partial data."""

from __future__ import annotations

from crypto_bot.models.market import ExchangeSnapshot, MarketTicker, WalletStatus
from crypto_bot.services.dw_service import has_rows as wallet_has_rows


def coalesce_snapshot(
    prev: ExchangeSnapshot | None,
    new: ExchangeSnapshot,
) -> ExchangeSnapshot:
    """Keep the best of two fetches — never replace good data with empty."""
    if prev is None or not prev.has_data:
        return new
    if not new.has_data:
        return prev

    spot = new.spot if new.spot is not None else prev.spot
    futures = _coalesce_futures(prev.futures, new.futures)
    wallet = new.wallet if (new.wallet and wallet_has_rows(new.wallet)) else prev.wallet

    return ExchangeSnapshot(
        key=new.key,
        name=new.name,
        futures_only=new.futures_only,
        spot=spot,
        futures=futures,
        wallet=wallet,
    )


def merge_snapshots(
    existing: list[ExchangeSnapshot],
    fetched: list[ExchangeSnapshot],
) -> list[ExchangeSnapshot]:
    by_key = {s.key: s for s in existing}
    for snap in fetched:
        by_key[snap.key] = coalesce_snapshot(by_key.get(snap.key), snap)
    return list(by_key.values())


def _coalesce_futures(
    prev: MarketTicker | None,
    new: MarketTicker | None,
) -> MarketTicker | None:
    if new is None:
        return prev
    if prev is None:
        return new
    rate = new.funding_rate if new.funding_rate is not None else prev.funding_rate
    ts = new.next_funding_ts if new.next_funding_ts is not None else prev.next_funding_ts
    return MarketTicker(
        price=new.price,
        url=new.url or prev.url,
        funding_rate=rate,
        next_funding_ts=ts,
    )
