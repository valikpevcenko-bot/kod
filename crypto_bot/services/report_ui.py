"""Telegram report loading / progress helpers."""

from __future__ import annotations

_LOADING_MARK = "🔎"

_LOADING_FOOTER = f"\n\n<i>{_LOADING_MARK} Loading exchanges…</i>"


def needs_more_exchanges(exchange_count: int, *, target: int) -> bool:
    return exchange_count < target


def with_loading_footer(text: str) -> str:
    if _LOADING_MARK in text:
        return text
    return text + _LOADING_FOOTER


def strip_loading_footer(text: str) -> str:
    idx = text.find(f"\n\n<i>{_LOADING_MARK}")
    if idx >= 0:
        return text[:idx].rstrip()
    return text
