"""Разбор тикера из команды /get."""

import re

from config import DEFAULT_QUOTE

# Популярные котируемые валюты (от длинных к коротким, чтобы корректно отрезать суффикс)
_QUOTES = ("USDT", "USDC", "BUSD", "USD", "BTC", "ETH", "EUR")


def parse_ticker(raw: str, default_quote: str = DEFAULT_QUOTE) -> tuple[str, str]:
    """
    Преобразует ввод пользователя в пару (base, quote).

    Примеры:
        BTCUSDT  -> (BTC, USDT)
        BTC/USDT -> (BTC, USDT)
        btc      -> (BTC, USDT)  # quote из DEFAULT_QUOTE
    """
    text = raw.strip().upper()
    if not text:
        raise ValueError("Пустой тикер")

    # Формат BASE/QUOTE
    if "/" in text:
        base, quote = text.split("/", 1)
        base, quote = base.strip(), quote.strip()
        if not base or not quote:
            raise ValueError("Неверный формат. Пример: BTCUSDT или BTC/USDT")
        return base, quote

    # Только база: BTC
    if re.fullmatch(r"[A-Z0-9]{1,15}", text):
        for quote in _QUOTES:
            if text.endswith(quote) and len(text) > len(quote):
                base = text[: -len(quote)]
                if base:
                    return base, quote
        # Не нашли суффикс — считаем, что это только базовый актив
        return text, default_quote.upper()

    raise ValueError("Тикер может содержать только буквы и цифры")


def to_ccxt_spot_symbol(base: str, quote: str) -> str:
    """Символ CCXT для спота: BTC/USDT."""
    return f"{base}/{quote}"


def to_ccxt_swap_symbol(base: str, quote: str) -> str:
    """Символ CCXT для USDT perpetual: BTC/USDT:USDT."""
    return f"{base}/{quote}:{quote}"
