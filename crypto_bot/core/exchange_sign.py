"""HMAC signing helpers for private exchange REST."""

from __future__ import annotations

import base64
import hashlib
import hmac
import time
from typing import Any
from urllib.parse import urlencode


def okx_sign(secret: str, timestamp: str, method: str, request_path: str, body: str = "") -> str:
    payload = f"{timestamp}{method}{request_path}{body}"
    digest = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).digest()
    return base64.b64encode(digest).decode()


def okx_timestamp_ms() -> str:
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc)
    ms = f"{now.microsecond // 1000:03d}"
    return now.strftime("%Y-%m-%dT%H:%M:%S.") + ms + "Z"


def bybit_sign(secret: str, payload: str) -> str:
    return hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()


def bybit_headers(api_key: str, secret: str, query: dict[str, str] | None = None) -> dict[str, str]:
    ts = str(int(time.time() * 1000))
    recv = "10000"
    param_str = urlencode(sorted((query or {}).items()))
    sign_payload = f"{ts}{api_key}{recv}{param_str}"
    return {
        "X-BAPI-API-KEY": api_key,
        "X-BAPI-SIGN": bybit_sign(secret, sign_payload),
        "X-BAPI-TIMESTAMP": ts,
        "X-BAPI-RECV-WINDOW": recv,
        "Content-Type": "application/json",
    }


def mexc_sign(secret: str, query: str) -> str:
    return hmac.new(secret.encode(), query.encode(), hashlib.sha256).hexdigest()


def bingx_sign(secret: str, payload: str) -> str:
    return hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()


def kraken_sign(secret_b64: str, path: str, data: dict[str, Any]) -> str:
    postdata = urlencode(data)
    encoded = (str(data["nonce"]) + postdata).encode()
    message = path.encode() + hashlib.sha256(encoded).digest()
    digest = hmac.new(base64.b64decode(secret_b64), message, hashlib.sha512).digest()
    return base64.b64encode(digest).decode()


def kraken_private_headers(api_key: str, secret_b64: str, path: str, data: dict[str, Any]) -> dict[str, str]:
    return {
        "API-Key": api_key,
        "API-Sign": kraken_sign(secret_b64, path, data),
        "Content-Type": "application/x-www-form-urlencoded",
    }


def bingx_timestamp_params(api_key: str, secret: str, params: dict[str, Any]) -> dict[str, Any]:
    ts = int(time.time() * 1000)
    full = {**params, "timestamp": ts}
    query = urlencode(sorted((k, str(v)) for k, v in full.items()))
    full["signature"] = bingx_sign(secret, query)
    return full
