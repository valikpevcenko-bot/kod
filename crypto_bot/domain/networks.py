"""Network labels for D/W display."""

from __future__ import annotations

from crypto_bot.domain.exchanges import DISPLAY_CONTRACT_NETWORKS

# Native L1 for ticker (SOL spot on Solana + wrapped on BSC, etc.)
NATIVE_NETWORK_BY_SYMBOL: dict[str, str] = {
    "SOL": "SOL",
    "ETH": "ETH",
    "BTC": "BTC",
    "TRX": "TRON",
    "TON": "TON",
    "DOGE": "DOGE",
    "XRP": "XRP",
    "ADA": "ADA",
    "DOT": "DOT",
    "AVAX": "AVAX",
    "LTC": "LTC",
    "BCH": "BCH",
    "NEAR": "NEAR",
    "APT": "APT",
    "SUI": "SUI",
}


def native_network_for_symbol(symbol: str) -> str | None:
    return NATIVE_NETWORK_BY_SYMBOL.get(symbol.upper())


def dw_display_networks(symbol: str, contract_networks: list[str]) -> list[str]:
    """EVM from contracts + native L1 when coin has both (e.g. SOL on BSC + Solana)."""
    targets = [n for n in contract_networks if n in DISPLAY_CONTRACT_NETWORKS]
    native = native_network_for_symbol(symbol)
    if native and native not in targets:
        targets.append(native)
    return targets
