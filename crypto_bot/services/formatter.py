"""Telegram HTML report formatter."""

from __future__ import annotations

import html
import time
from collections.abc import Iterable

import structlog

from crypto_bot.clients.coinmarketcap import filter_display_contracts
from crypto_bot.domain.exchanges import EXCHANGE_DEFS
from crypto_bot.models.market import (
    ContractInfo,
    ExchangeSnapshot,
    MarketTicker,
    NetworkWallet,
    WalletStatus,
)

logger = structlog.get_logger(__name__)


def format_report(
    base: str,
    quote: str,
    snapshots: Iterable[ExchangeSnapshot],
    contracts: list[ContractInfo] | None = None,
) -> str:
    items = _ordered_items(snapshots)
    ticker = html.escape(base.upper())

    if not items:
        return (
            f"<b>{ticker}</b>\n\n"
            f"❌ Not listed on tracked exchanges.\n"
            f"Try: <code>{html.escape(base)}{html.escape(quote)}</code>"
        )

    lines = [f"<code>{ticker}</code>", ""]
    lines.extend(_contracts_block(contracts or [], base))
    lines.append(f"<b>Exchanges ({len(items)}):</b>")
    lines.append("")

    for snap in items:
        header = _exchange_line(snap)
        if header:
            lines.append(header)
        dw = _network_lines(snap.wallet, snap.futures_only, base, snap.key)
        if dw:
            lines.extend(dw)
        lines.append("")

    return "\n".join(lines).strip()


def format_report_safe(
    base: str,
    quote: str,
    snapshots: Iterable[ExchangeSnapshot],
    contracts: list[ContractInfo] | None = None,
) -> str:
    try:
        safe_snaps = [s for s in snapshots if isinstance(s, ExchangeSnapshot) and s.has_data]
        safe_cont = [c for c in (contracts or []) if isinstance(c, ContractInfo)]
        return format_report(base, quote, safe_snaps, safe_cont or None)
    except Exception:
        logger.exception("format_report_failed", base=base)
        ticker = html.escape(base.upper())
        return f"<code>{ticker}</code>\n\n❌ Report formatting error."


def _ordered_items(snapshots: Iterable[ExchangeSnapshot]) -> list[ExchangeSnapshot]:
    by_key = {s.key: s for s in snapshots if s.has_data}
    out: list[ExchangeSnapshot] = []
    for defn in EXCHANGE_DEFS:
        if defn.key in by_key:
            out.append(by_key[defn.key])
    for s in snapshots:
        if s.has_data and s.key not in {x.key for x in out}:
            out.append(s)
    return out


def _fmt_price(value: float) -> str:
    if value >= 1000:
        s = f"{value:,.2f}"
    elif value >= 1:
        s = f"{value:.4f}"
    else:
        s = f"{value:.4f}"
    return f"${s.rstrip('0').rstrip('.')}"


def _price_plain(value: float) -> str:
    return html.escape(_fmt_price(value))


def _icon(flag: bool | None) -> str:
    if flag is True:
        return "✅"
    if flag is False:
        return "❌"
    return "❓"


def _contracts_block(contracts: list[ContractInfo], coin: str) -> list[str]:
    shown = filter_display_contracts(contracts, coin=coin)
    rows: list[str] = []
    if shown:
        from crypto_bot.clients.coinmarketcap import is_native

        for contract in shown:
            addr = contract.address.strip()
            if is_native(addr):
                continue
            rows.append(
                f"• {html.escape(contract.network)}: "
                f"<code>{html.escape(addr)}</code>"
            )
    if not rows:
        return []
    return ["<b>Contracts:</b>", *rows, ""]


def _fmt_funding_pct(rate: float) -> str:
    pct = rate * 100
    s = f"{pct:+.4f}".rstrip("0").rstrip(".")
    if s in ("+0", "-0", "0"):
        s = "+0"
    return f"{s}%"


def _fmt_countdown(next_ts: int | None) -> str:
    if not next_ts:
        return ""
    now = time.time()
    target = next_ts / 1000 if next_ts > 1_000_000_000_000 else float(next_ts)
    left = max(0, int(target - now))
    h, rem = divmod(left, 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


def _funding_html(fut: MarketTicker) -> str:
    timer = _fmt_countdown(fut.next_funding_ts)
    if fut.funding_rate is not None and timer:
        inner = f"{_fmt_funding_pct(fut.funding_rate)} / {timer}"
    elif fut.funding_rate is not None:
        inner = _fmt_funding_pct(fut.funding_rate)
    elif timer:
        inner = timer
    else:
        return ""
    return f"<i>[{html.escape(inner)}]</i>"


def _html_name(name: str) -> str:
    if name == "Gate.io":
        return "Gate&#46;io"
    return html.escape(name)


def _exchange_name_html(name: str, spot: MarketTicker | None) -> str:
    """Link exchange name to spot market when spot data exists."""
    label = _html_name(name)
    if spot is not None and spot.url:
        return f'<a href="{html.escape(spot.url)}"><b>{label}</b></a>'
    return f"<b>{label}</b>"


def _futures_label_html(fut: MarketTicker, *, prefix: str = " | ") -> str:
    label = "Futures"
    if fut.url:
        return f'{prefix}<a href="{html.escape(fut.url)}"><b>{label}</b></a>'
    return f"{prefix}<b>{label}</b>"


def _spot_price_html(spot: MarketTicker) -> str:
    return f" {_price_plain(spot.price)}"


def _exchange_line(snap: ExchangeSnapshot) -> str:
    if not snap.has_data:
        return ""
    if snap.spot is not None:
        parts: list[str] = ["• ", _exchange_name_html(snap.name, snap.spot)]
        parts.append(_spot_price_html(snap.spot))
        if snap.futures:
            parts.append(_futures_label_html(snap.futures))
            parts.append(f" {_price_plain(snap.futures.price)}")
            fund = _funding_html(snap.futures)
            if fund:
                parts.append(f" {fund}")
        return "".join(parts)

    if snap.futures:
        parts = ["• ", _exchange_name_html(snap.name, None)]
        parts.append(_futures_label_html(snap.futures, prefix=" · "))
        parts.append(f" {_price_plain(snap.futures.price)}")
        fund = _funding_html(snap.futures)
        if fund:
            parts.append(f" {fund}")

    return "".join(parts)


def _network_label(net: NetworkWallet, symbol: str, exchange_key: str) -> str:
    from crypto_bot.domain.network_registry import NETWORK_DISPLAY

    api_coin = (net.exchange_coin or "").strip().upper()
    if exchange_key == "bingx" and api_coin and api_coin != symbol.upper():
        return f"{net.network} · {api_coin}"
    return NETWORK_DISPLAY.get(net.network, net.network)


def _network_lines(
    wallet: WalletStatus | None,
    futures_only: bool,
    symbol: str,
    exchange_key: str = "",
) -> list[str]:
    if futures_only or not wallet or not wallet.networks:
        return []
    nets = [n for n in wallet.networks if n.deposit is not None or n.withdraw is not None]
    if not nets:
        return []
    lines: list[str] = []
    for net in nets:
        label = _network_label(net, symbol, exchange_key)
        lines.append(
            f"• {html.escape(label)}: D {_icon(net.deposit)} | W {_icon(net.withdraw)}"
        )
    return lines
