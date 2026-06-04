"""Domain models for market reports."""

from __future__ import annotations

from pydantic import BaseModel, Field


class MarketTicker(BaseModel):
    price: float
    url: str | None = None
    funding_rate: float | None = None
    next_funding_ts: int | None = None


class NetworkWallet(BaseModel):
    network: str
    exchange_coin: str | None = None
    deposit: bool | None = None
    withdraw: bool | None = None
    min_deposit: str | None = None
    min_withdraw: str | None = None
    withdraw_fee: str | None = None


class WalletStatus(BaseModel):
    networks: list[NetworkWallet] = Field(default_factory=list)
    note: str | None = None


class ContractInfo(BaseModel):
    network: str
    address: str


class ExchangeSnapshot(BaseModel):
    key: str
    name: str
    spot: MarketTicker | None = None
    futures: MarketTicker | None = None
    wallet: WalletStatus | None = None
    futures_only: bool = False

    @property
    def has_data(self) -> bool:
        return self.spot is not None or self.futures is not None


class CachedReport(BaseModel):
    text: str
    snapshots: list[ExchangeSnapshot]
    contracts: list[ContractInfo]
    complete: bool = False
