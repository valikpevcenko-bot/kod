"""API response guards (int/str codes, D/W flags)."""

from __future__ import annotations

from typing import Any


def code_ok(code: Any, *accepted: Any) -> bool:
    if not accepted:
        accepted = (0, "0")
    s = str(code).strip() if code is not None else ""
    for item in accepted:
        if code == item:
            return True
        if s and s == str(item).strip():
            return True
    return False


def ret_ok(code: Any) -> bool:
    return code_ok(code, 0, "0")


def okx_ok(code: Any) -> bool:
    return code_ok(code, 0, "0")


def kucoin_ok(code: Any) -> bool:
    return code_ok(code, 0, "0", 200000, "200000")


def dw_flag(raw: Any) -> bool | None:
    """Only explicit API values — never guess open/closed."""
    if raw is None:
        return None
    if isinstance(raw, bool):
        return raw
    text = str(raw).strip().lower()
    if text in ("1", "true", "yes", "on", "enabled", "allow", "allowed"):
        return True
    if text in ("0", "false", "no", "off", "disabled", "deny", "denied"):
        return False
    if raw in (1,):
        return True
    if raw in (0,):
        return False
    return None


def float_field(value: Any) -> float | None:
    """Parse numeric API fields; allows 0 (e.g. funding rate)."""
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        return float(text)
    except (TypeError, ValueError):
        return None


def price_positive(value: Any) -> float | None:
    if value is None:
        return None
    try:
        price = float(value)
        return price if price > 0 else None
    except (TypeError, ValueError):
        return None


def dict_field_price(data: Any, *fields: str) -> float | None:
    if not isinstance(data, dict):
        return None
    for field in fields:
        if field in data:
            price = price_positive(data.get(field))
            if price is not None:
                return price
    return None


def list_row_price(rows: Any, *fields: str) -> float | None:
    if not isinstance(rows, list) or not rows or not isinstance(rows[0], dict):
        return None
    return dict_field_price(rows[0], *fields)
