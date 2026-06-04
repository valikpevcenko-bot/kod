"""Deposit / withdrawal network models."""

from __future__ import annotations

from pydantic import BaseModel, Field

from crypto_bot.models.market import NetworkWallet, WalletStatus


class NetworkDwStatus(BaseModel):
    """One chain/network on an exchange for a coin."""

    network: str
    exchange_coin: str | None = None
    deposit: bool | None = None
    withdraw: bool | None = None
    min_deposit: str | None = None
    min_withdraw: str | None = None
    withdraw_fee: str | None = None

    def to_wallet_row(self) -> NetworkWallet:
        return NetworkWallet(
            network=self.network,
            exchange_coin=self.exchange_coin,
            deposit=self.deposit,
            withdraw=self.withdraw,
            min_deposit=self.min_deposit,
            min_withdraw=self.min_withdraw,
            withdraw_fee=self.withdraw_fee,
        )


class ExchangeDwResult(BaseModel):
    exchange_key: str
    symbol: str
    networks: list[NetworkDwStatus] = Field(default_factory=list)
    note: str | None = None

    def to_wallet_status(self) -> WalletStatus:
        return WalletStatus(
            networks=[n.to_wallet_row() for n in self.networks],
            note=self.note,
        )
