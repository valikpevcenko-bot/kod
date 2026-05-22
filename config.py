"""Загрузка настроек из .env."""

import os
import sys
from typing import Any

from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN: str | None = os.getenv("BOT_TOKEN")
DEFAULT_QUOTE: str = os.getenv("DEFAULT_QUOTE", "USDT")
CMC_API_KEY: str | None = os.getenv("CMC_API_KEY")


def _pair(key: str, secret: str, password: str | None = None) -> dict[str, str]:
    """Собирает dict для CCXT, если заданы key и secret."""
    api_key = os.getenv(key)
    api_secret = os.getenv(secret)
    if not api_key or not api_secret:
        return {}
    out: dict[str, str] = {"apiKey": api_key, "secret": api_secret}
    if password:
        pwd = os.getenv(password)
        if pwd:
            out["password"] = pwd
    return out


def credentials_for(ccxt_id: str) -> dict[str, Any]:
    """
    Read-only API ключи бирж (для D/W).
    ccxt_id: binance | bybit | okx | mexc | bingx | bitget | kucoin | htx
    """
    mapping = {
        "binance": _pair("BINANCE_API_KEY", "BINANCE_API_SECRET"),
        "bybit": _pair("BYBIT_API_KEY", "BYBIT_API_SECRET"),
        "okx": _pair("OKX_API_KEY", "OKX_API_SECRET", "OKX_API_PASSPHRASE"),
        "mexc": _pair("MEXC_API_KEY", "MEXC_API_SECRET"),
        "bingx": _pair("BINGX_API_KEY", "BINGX_API_SECRET"),
        "bitget": _pair("BITGET_API_KEY", "BITGET_API_SECRET", "BITGET_API_PASSPHRASE"),
        "kucoin": _pair("KUCOIN_API_KEY", "KUCOIN_API_SECRET", "KUCOIN_API_PASSPHRASE"),
        "htx": _pair("HTX_API_KEY", "HTX_API_SECRET"),
        "huobi": _pair("HTX_API_KEY", "HTX_API_SECRET"),
    }
    return mapping.get(ccxt_id, {})


def require_token() -> str:
    if not BOT_TOKEN:
        print(
            "❌ Не найден BOT_TOKEN.\n"
            "   1. Скопируй .env.example в .env\n"
            "   2. Вставь токен от @BotFather",
            file=sys.stderr,
        )
        sys.exit(1)
    return BOT_TOKEN
