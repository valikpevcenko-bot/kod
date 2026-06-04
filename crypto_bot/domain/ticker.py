"""Ticker parsing utilities."""

from __future__ import annotations

import re

_QUOTES = ("USDT", "USDC", "BUSD", "USD", "BTC", "ETH", "EUR")


def parse_ticker(raw: str, default_quote: str = "USDT") -> tuple[str, str]:
    """
    Parse user input into (base, quote).

    Examples:
        BTCUSDT -> (BTC, USDT)
        BTC/USDT -> (BTC, USDT)
        sol -> (SOL, USDT)
    """
    text = raw.strip().upper()
    if not text:
        raise ValueError("Empty ticker")

    if "/" in text:
        base, quote = text.split("/", 1)
        base, quote = base.strip(), quote.strip()
        if not base or not quote:
            raise ValueError("Invalid format. Example: BTCUSDT or BTC/USDT")
        return base, quote

    if re.fullmatch(r"[A-Z0-9]{1,15}", text):
        for quote in _QUOTES:
            if text.endswith(quote) and len(text) > len(quote):
                base = text[: -len(quote)]
                if base:
                    return base, quote
        return text, default_quote.upper()

    raise ValueError("Ticker may contain only letters and digits")


def trading_pair(base: str, quote: str) -> tuple[str, str, str]:
    b, q = base.upper(), quote.upper()
    return b, q, f"{b}{q}"
