"""Canonical blockchain network IDs — never confuse token ticker with chain."""

from __future__ import annotations

import re

# Canonical labels shown in Telegram (BSC, ETH, SOL, …)
# Подпись в отчёте (чтобы не путать BSC и Bitcoin на BingX)
NETWORK_DISPLAY: dict[str, str] = {
    "BTC": "Bitcoin",
    "BSC": "BSC (BEP20)",
    "ETH": "Ethereum (ERC20)",
    "SOL": "Solana",
    "TRON": "TRON (TRC20)",
    "LIGHTNING": "Lightning",
    "SEGWITBTC": "SegWit",
    "BASE": "Base",
    "ARBITRUM": "Arbitrum",
    "OPTIMISM": "Optimism",
    "POLYGON": "Polygon",
}

CANONICAL_NETWORKS: frozenset[str] = frozenset(
    {
        "BTC",
        "ETH",
        "BSC",
        "SOL",
        "BASE",
        "ARBITRUM",
        "OPTIMISM",
        "POLYGON",
        "MANTLE",
        "TRON",
        "AVAX",
        "TON",
        "APT",
        "SUI",
        "NEAR",
        "DOGE",
        "XRP",
        "LTC",
        "BCH",
        "LIGHTNING",
        "SEGWITBTC",
        "OKTC",
        "KCC",
        "WLD",
        "STX",
        "STACKS",
    }
)

# Exchange raw label -> canonical (longer keys first in matcher)
_ALIASES: dict[str, str] = {
    "BNB SMART CHAIN (BEP20)": "BSC",
    "BINANCE SMART CHAIN": "BSC",
    "BNB SMART CHAIN": "BSC",
    "WORLD CHAIN": "WLD",
    "WORLDCHAIN": "WLD",
    "LIGHTNING NETWORK": "LIGHTNING",
    "ARBITRUM ONE": "ARBITRUM",
    "BEP20": "BSC",
    "ERC20": "ETH",
    "TRC20": "TRON",
    "OPETH": "OPTIMISM",
    "ARBONE": "ARBITRUM",
    "MATIC": "POLYGON",
    "SOLANA": "SOL",
    "ETHEREUM": "ETH",
    "BITCOIN": "BTC",
    "BTC-SEGWIT": "SEGWITBTC",
    "SEGWIT": "SEGWITBTC",
    "BECH32": "SEGWITBTC",
    "AVALANCHE": "AVAX",
    "TONCOIN": "TON",
    "APTOS": "APT",
    "SEGWITBTC": "SEGWITBTC",
    "BTCLIGHTNING": "LIGHTNING",
    "BSC": "BSC",
    "ETH": "ETH",
    "SOL": "SOL",
    "BASE": "BASE",
    "ARBITRUM": "ARBITRUM",
    "ARB": "ARBITRUM",
    "OPTIMISM": "OPTIMISM",
    "OP": "OPTIMISM",
    "POLYGON": "POLYGON",
    "MANTLE": "MANTLE",
    "TRON": "TRON",
    "TRX": "TRON",
    "AVAX": "AVAX",
    "BTC": "BTC",
    "TON": "TON",
    "APT": "APT",
    "SUI": "SUI",
    "NEAR": "NEAR",
    "DOGE": "DOGE",
    "XRP": "XRP",
    "LTC": "LTC",
    "BCH": "BCH",
    "LIGHTNING": "LIGHTNING",
    "OKTC": "OKTC",
    "KCC": "KCC",
    "WLD": "WLD",
    "STX": "STX",
    "STACKS": "STX",
    "STACKS LAYER": "STX",
}

# Coin tickers that must never be used as network names when they match the asset
_COIN_LIKE_SKIP = re.compile(r"^[A-Z0-9]{2,12}$")


def _match_alias(text: str) -> str | None:
    t = text.strip().upper()
    if not t:
        return None
    if t in _ALIASES:
        return _ALIASES[t]
    for key in sorted(_ALIASES, key=len, reverse=True):
        if len(key) < 3:
            continue
        if key == "SOL" and "OPTIMISM" in t:
            continue
        if key == "ETH" and t in ("OPETH", "OPTIMISM"):
            continue
        if key in t:
            return _ALIASES[key]
    return None


def is_token_ticker(value: str, coin: str) -> bool:
    """True if value is the asset symbol, not a chain (MIRA on MIRA), but BTC on BTC is OK."""
    v = value.strip().upper()
    c = coin.strip().upper()
    if not v or not c:
        return False
    if v == c:
        # Native L1: BTC/BTC, ETH/ETH — same ticker as chain name
        if v in CANONICAL_NETWORKS:
            return False
        return True
    if v in (f"{c}NETWORK", f"{c}NET", f"{c}COIN"):
        return True
    return False


def resolve_network(
    raw: str,
    *,
    coin: str,
    exchange: str = "",
) -> str | None:
    """
    Map exchange chain field to canonical network id.
    Returns None if label is empty, unknown, or confused with token ticker.
    """
    text = str(raw or "").strip()
    if not text:
        return None
    if is_token_ticker(text, coin):
        return None

    hit = _match_alias(text)
    if hit:
        if is_token_ticker(hit, coin):
            return None
        return hit

    upper = text.upper().replace(" ", "")
    if is_token_ticker(upper, coin):
        return None
    if upper in CANONICAL_NETWORKS:
        return upper

    if len(text) <= 24 and "/" not in text:
        return upper[:20]

    return None


def network_display_label(network: str, *, exchange_coin: str = "", symbol: str = "") -> str:
    """Human label for Telegram (BingX: BSC + MIRANETWORK asset)."""
    net = (network or "").strip().upper()
    label = NETWORK_DISPLAY.get(net, net)
    api = (exchange_coin or "").strip().upper()
    sym = (symbol or "").strip().upper()
    if api and api != sym and api not in (sym, f"{sym}NETWORK", f"{sym}NET"):
        return f"{label} · coin {api}"
    return label


def merge_network_status(
    existing: dict[str, tuple[bool | None, bool | None]],
    network: str,
    deposit: bool | None,
    withdraw: bool | None,
) -> None:
    """Merge D/W flags per canonical network (True wins over False/None)."""
    prev = existing.get(network)
    if prev is None:
        existing[network] = (deposit, withdraw)
        return
    dep, wdr = prev
    new_dep = deposit if deposit is True else dep
    new_wdr = withdraw if withdraw is True else wdr
    existing[network] = (new_dep, new_wdr)
