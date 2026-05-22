"""
Формат как на референсе: контракты + биржи с D/W по сетям.
"""

import html
from typing import Iterable, Optional

from models import ContractInfo, ExchangeSnapshot, NetworkWallet, WalletStatus


def _fmt_price(value: float) -> str:
    if value >= 1000:
        s = f"{value:,.2f}"
    elif value >= 1:
        s = f"{value:.4f}"
    else:
        s = f"{value:.4f}"
    return f"${s.rstrip('0').rstrip('.')}"


def _icon(flag: Optional[bool]) -> str:
    if flag is True:
        return "✅"
    if flag is False:
        return "❌"
    return "❓"


def _contracts_block(contracts: list[ContractInfo]) -> list[str]:
    """Блок контрактов — всегда в сообщении."""
    lines = ["<b>Контракти:</b>"]
    if contracts:
        for c in contracts:
            addr = c.address.strip()
            lines.append(
                f"• {html.escape(c.network)}: <code>{html.escape(addr)}</code>"
            )
    else:
        lines.append(
            "<i>немає EVM-контракту (нативна монета або дуже новий токен)</i>"
        )
    lines.append("")
    return lines


def _network_lines(wallet: Optional[WalletStatus], futures_only: bool = False) -> list[str]:
    if futures_only or not wallet or not wallet.networks:
        return []
    lines: list[str] = []
    for net in wallet.networks:
        # Без данных не показываем ❓ — только реальные статусы
        if net.deposit is None and net.withdraw is None:
            continue
        d = _icon(net.deposit)
        w = _icon(net.withdraw)
        lines.append(f"• {html.escape(net.network)}: D {d} | W {w}")
    return lines


def _exchange_line(snap: ExchangeSnapshot) -> str:
    """• Bybit ($0.28) | Futures ($0.27)"""
    parts: list[str] = ["• "]

    if snap.futures_only and snap.futures:
        f = snap.futures
        parts.append(f"<b>{html.escape(snap.name)}</b>:")
        if f.url:
            parts.append(
                f' <a href="{html.escape(f.url)}">Futures</a> ({_fmt_price(f.price)})'
            )
        else:
            parts.append(f" Futures ({_fmt_price(f.price)})")
        return "".join(parts)

    if snap.spot:
        name = html.escape(snap.name)
        if snap.spot.url:
            parts.append(f'<a href="{html.escape(snap.spot.url)}"><b>{name}</b></a>')
        else:
            parts.append(f"<b>{name}</b>")
        parts.append(f" ({_fmt_price(snap.spot.price)})")
    elif snap.futures:
        parts.append(f"<b>{html.escape(snap.name)}</b>")
    else:
        return ""

    if snap.wallet and snap.wallet.note:
        parts.append(f" <i>({html.escape(snap.wallet.note)})</i>")

    if snap.futures:
        f = snap.futures
        if f.url:
            parts.append(
                f' | <a href="{html.escape(f.url)}">Futures</a> ({_fmt_price(f.price)})'
            )
        else:
            parts.append(f" | Futures ({_fmt_price(f.price)})")

    return "".join(parts)


def format_report(
    base: str,
    quote: str,
    snapshots: Iterable[ExchangeSnapshot],
    contracts: Optional[list[ContractInfo]] = None,
) -> str:
    items = [s for s in snapshots if s.has_data]
    ticker = html.escape(base.upper())

    if not items:
        return (
            f"<b>{ticker}</b>\n\n"
            f"❌ Монета не знайдена на біржах.\n"
            f"Спробуй: <code>{html.escape(base)}{html.escape(quote)}</code>"
        )

    lines = [f"<b>{ticker}</b>", ""]
    lines.extend(_contracts_block(contracts or []))
    lines.append(f"<b>Біржі ({len(items)}):</b>")
    lines.append("")

    for snap in items:
        header = _exchange_line(snap)
        if header:
            lines.append(header)
        lines.extend(_network_lines(snap.wallet, snap.futures_only))
        lines.append("")

    return "\n".join(lines).strip()
