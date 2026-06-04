"""Normalize exchange network names for display (delegates to network_registry)."""

from __future__ import annotations

from crypto_bot.domain.network_registry import resolve_network

_CHAIN_ALIASES: dict[str, str] = {
    "BSC": "BSC",
    "BEP20": "BSC",
    "BNB SMART CHAIN": "BSC",
    "BINANCE SMART CHAIN": "BSC",
    "BNB": "BSC",
    "ETH": "ETH",
    "ERC20": "ETH",
    "ETHEREUM": "ETH",
    "ARBITRUM": "ARBITRUM",
    "ARB": "ARBITRUM",
    "ARBONE": "ARBITRUM",
    "ARBITRUM ONE": "ARBITRUM",
    "OPTIMISM": "OPTIMISM",
    "OPETH": "OPTIMISM",
    "OP": "OPTIMISM",
    "WORLD CHAIN": "WLD",
    "WORLDCHAIN": "WLD",
    "POLYGON": "POLYGON",
    "MATIC": "POLYGON",
    "MANTLE": "MANTLE",
    "SOL": "SOL",
    "SOLANA": "SOL",
    "TRX": "TRON",
    "TRON": "TRON",
    "TRC20": "TRON",
    "BASE": "BASE",
    "AVAX": "AVAX",
    "AVALANCHE": "AVAX",
    "BTC": "BTC",
    "BITCOIN": "BTC",
    "SEGWITBTC": "SEGWITBTC",
    "BTC(SegWit)": "SEGWITBTC",
    "LIGHTNING": "LIGHTNING",
    "LIGHTNING NETWORK": "LIGHTNING",
    "BTCLIGHTNING": "LIGHTNING",
    "BCH": "BCH",
    "LTC": "LTC",
    "DOGE": "DOGE",
    "XRP": "XRP",
    "TON": "TON",
    "TONCOIN": "TON",
    "APT": "APT",
    "APTOS": "APT",
    "SUI": "SUI",
    "NEAR": "NEAR",
    "OKTC": "OKTC",
    "X LAYER": "X Layer",
    "XLAYER": "X Layer",
    "KCC": "KCC",
    "KCC MAINNET": "KCC",
}


def _match_chain_label(text: str) -> str | None:
    t = text.strip().upper()
    if not t:
        return None
    if t in _CHAIN_ALIASES:
        return _CHAIN_ALIASES[t]
    for key in sorted(_CHAIN_ALIASES, key=len, reverse=True):
        if len(key) < 3:
            continue
        if key == "SOL" and "OPTIMISM" in t:
            continue
        if key == "ETH" and t in ("OPETH", "OPTIMISM"):
            continue
        if key in t:
            return _CHAIN_ALIASES[key]
    return None


def normalize_network_label(raw: str, *, coin: str = "") -> str:
    """Map API label to short display name; keep unknown chains readable."""
    text = str(raw or "").strip()
    if not text:
        return "OTHER"
    if coin:
        hit = resolve_network(text, coin=coin)
        if hit:
            return hit
    hit = _match_chain_label(text)
    if hit:
        return hit
    if len(text) <= 24:
        return text.upper().replace(" ", "")
    return text.upper().split()[0][:20]


def sort_network_rows(networks: list) -> list:
    """Native / major chains first, then alphabetical."""

    def _key(n) -> tuple[int, str]:
        name = getattr(n, "network", str(n))
        priority = {
            "BTC": 0,
            "ETH": 1,
            "WLD": 2,
            "BSC": 3,
            "SOL": 4,
            "OPTIMISM": 5,
            "TRON": 6,
            "ARBITRUM": 7,
            "LIGHTNING": 8,
            "SEGWITBTC": 9,
        }
        return (priority.get(name, 50), name)

    return sorted(networks, key=_key)
