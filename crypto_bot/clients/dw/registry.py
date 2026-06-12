"""Registry of deposit/withdrawal clients."""

from __future__ import annotations

from crypto_bot.clients.dw.base import DepositWithdrawalClient
from crypto_bot.clients.dw.binance import BinanceDwClient
from crypto_bot.clients.dw.bingx import BingxDwClient
from crypto_bot.clients.dw.bitget import BitgetDwClient
from crypto_bot.clients.dw.bybit import BybitDwClient
from crypto_bot.clients.dw.gate import GateDwClient
from crypto_bot.clients.dw.kraken import KrakenDwClient
from crypto_bot.clients.dw.kucoin import KucoinDwClient
from crypto_bot.clients.dw.mexc import MexcDwClient
from crypto_bot.clients.dw.okx import OkxDwClient
from crypto_bot.core.http import HttpClient

DW_EXCHANGE_KEYS: frozenset[str] = frozenset(
    {
        "binance",
        "bybit",
        "okx",
        "mexc",
        "gate",
        "bitget",
        "kucoin",
        "bingx",
        "kraken",
    }
)


def build_dw_clients(http: HttpClient) -> dict[str, DepositWithdrawalClient]:
    clients: list[DepositWithdrawalClient] = [
        BinanceDwClient(http),
        BybitDwClient(http),
        OkxDwClient(http),
        MexcDwClient(http),
        GateDwClient(http),
        BitgetDwClient(http),
        KucoinDwClient(http),
        BingxDwClient(http),
        KrakenDwClient(http),
    ]
    return {c.exchange_key: c for c in clients}
