"""Модели данных для ответа бота."""

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class MarketTicker:
    price: float
    url: Optional[str] = None


@dataclass
class NetworkWallet:
    """D/W по одной сети (BSC, ETH, …)."""

    network: str
    deposit: Optional[bool] = None
    withdraw: Optional[bool] = None


@dataclass
class WalletStatus:
    networks: list[NetworkWallet] = field(default_factory=list)
    note: Optional[str] = None  # «Депозити відкриті»


@dataclass
class ContractInfo:
    network: str
    address: str


@dataclass
class ExchangeSnapshot:
    key: str
    name: str
    spot: Optional[MarketTicker] = None
    futures: Optional[MarketTicker] = None
    wallet: Optional[WalletStatus] = None
    futures_only: bool = False

    @property
    def has_data(self) -> bool:
        return self.spot is not None or self.futures is not None
