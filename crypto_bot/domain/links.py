"""Trading pair deep links."""

from __future__ import annotations


def kucoin_futures_symbol(base: str, quote: str) -> str:
    b, q = base.upper(), quote.upper()
    if b == "BTC":
        return "XBTUSDTM"
    return f"{b}{q}M"


def spot_url(exchange_key: str, base: str, quote: str) -> str | None:
    b, q = base.upper(), quote.upper()
    bl, ql = base.lower(), quote.lower()
    templates = {
        "binance": f"https://www.binance.com/en/trade/{b}_{q}",
        "bybit": f"https://www.bybit.com/trade/spot/{b}/{q}",
        "gate": f"https://www.gate.io/trade/{b}_{q}",
        "mexc": f"https://www.mexc.com/exchange/{b}_{q}",
        "bitget": f"https://www.bitget.com/spot/{b}{q}",
        "okx": f"https://www.okx.com/trade-spot/{bl}-{ql}",
        "kucoin": f"https://www.kucoin.com/trade/{b}-{q}",
        "bingx": f"https://bingx.com/en-us/spot/{b}-{q}",
        "htx": f"https://www.htx.com/trade/{bl}_{ql}",
        "aster": f"https://www.asterdex.com/en/spot/{b}{q}",
    }
    return templates.get(exchange_key)


def futures_url(exchange_key: str, base: str, quote: str) -> str | None:
    b, q = base.upper(), quote.upper()
    bl, ql = base.lower(), quote.lower()
    fut_sym = kucoin_futures_symbol(base, quote).replace("USDTM", "USDT")
    templates = {
        "binance": f"https://www.binance.com/en/futures/{b}{q}",
        "bybit": f"https://www.bybit.com/trade/usdt/{b}{q}",
        "gate": f"https://www.gate.io/futures/{q}/{b}_{q}",
        "mexc": f"https://www.mexc.com/futures/{b}_{q}",
        "bitget": f"https://www.bitget.com/futures/usdt/{b}{q}",
        "okx": f"https://www.okx.com/trade-swap/{bl}-{ql}-swap",
        "kucoin": f"https://www.kucoin.com/futures/trade/{fut_sym}",
        "bingx": f"https://bingx.com/en-us/perpetual/{b}-{q}",
        "htx": f"https://www.htx.com/futures/linear_swap/exchange#contract_code={b}-{q}",
        "aster": f"https://www.asterdex.com/en/futures/{b}{q}",
        "hyperliquid": f"https://app.hyperliquid.xyz/trade/{b}",
    }
    return templates.get(exchange_key)
