"""Shared async HTTP client with retry and rate-limit awareness."""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
import structlog

from crypto_bot.config.settings import get_settings
from crypto_bot.core.price_fast import price_fast_ctx
from crypto_bot.core.retry import with_exponential_backoff

logger = structlog.get_logger(__name__)

_HTTP_LIMITS = httpx.Limits(
    max_connections=220,
    max_keepalive_connections=96,
    keepalive_expiry=45.0,
)


class HttpClient:
    """Process-scoped httpx.AsyncClient wrapper."""

    def __init__(self) -> None:
        settings = get_settings()
        connect = settings.http_connect_timeout
        read = settings.http_timeout
        if settings.turbo_mode or settings.asia_vps:
            connect = min(connect, 0.15)
            read = min(read, 0.42)
        self._timeout = httpx.Timeout(read, connect=connect)
        self._max_retries = settings.http_max_retries
        self._proxy = settings.outbound_proxy()
        self._client: httpx.AsyncClient | None = None
        self._lock = asyncio.Lock()

    async def start(self) -> None:
        async with self._lock:
            if self._client is None:
                kwargs: dict[str, Any] = {
                    "timeout": self._timeout,
                    "limits": _HTTP_LIMITS,
                    "follow_redirects": True,
                    "headers": {"User-Agent": "crypto-telegram-bot/1.0"},
                }
                if self._proxy:
                    kwargs["proxy"] = self._proxy
                self._client = httpx.AsyncClient(**kwargs)

    async def close(self) -> None:
        async with self._lock:
            if self._client is not None:
                await self._client.aclose()
                self._client = None

    def _ensure(self) -> httpx.AsyncClient:
        if self._client is None:
            raise RuntimeError("HttpClient not started — call start() first")
        return self._client

    async def get_json(
        self,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        timeout: float | None = None,
    ) -> Any:
        return await self._request("GET", url, params=params, headers=headers, timeout=timeout)

    async def post_json(
        self,
        url: str,
        *,
        json_body: Any = None,
        headers: dict[str, str] | None = None,
        timeout: float | None = None,
    ) -> Any:
        return await self._request(
            "POST",
            url,
            json_body=json_body,
            headers=headers,
            timeout=timeout,
        )

    async def post_form(
        self,
        url: str,
        *,
        data: dict[str, Any],
        headers: dict[str, str] | None = None,
        timeout: float | None = None,
    ) -> Any:
        client = self._ensure()
        req_timeout = httpx.Timeout(timeout or get_settings().http_timeout)

        async def _once() -> Any:
            response = await client.post(
                url, data=data, headers=headers, timeout=req_timeout
            )
            if response.status_code >= 400:
                return None
            return response.json()

        attempts = 1 if price_fast_ctx.get() else self._max_retries
        try:
            return await with_exponential_backoff(
                _once,
                max_attempts=attempts,
                base_delay=0.06,
                retry_on=(httpx.HTTPError, asyncio.TimeoutError),
            )
        except Exception as exc:
            logger.debug("http_fail", url=url[:60], error=str(exc)[:120])
            return None

    async def _request(
        self,
        method: str,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: Any = None,
        headers: dict[str, str] | None = None,
        timeout: float | None = None,
    ) -> Any:
        client = self._ensure()
        req_timeout = httpx.Timeout(timeout or get_settings().http_timeout)

        async def _once() -> Any:
            if method == "GET":
                response = await client.get(
                    url, params=params or {}, headers=headers, timeout=req_timeout
                )
            else:
                response = await client.post(
                    url, json=json_body, headers=headers, timeout=req_timeout
                )
            if response.status_code == 429:
                raise httpx.HTTPStatusError(
                    "rate limited",
                    request=response.request,
                    response=response,
                )
            if response.status_code >= 400:
                return None
            return response.json()

        attempts = 1 if price_fast_ctx.get() else self._max_retries
        try:
            return await with_exponential_backoff(
                _once,
                max_attempts=attempts,
                base_delay=0.06,
                retry_on=(httpx.HTTPError, asyncio.TimeoutError),
            )
        except Exception as exc:
            logger.debug("http_fail", url=url[:60], error=str(exc)[:120])
            return None


_http: HttpClient | None = None


def get_http() -> HttpClient:
    global _http
    if _http is None:
        _http = HttpClient()
    return _http


async def close_http() -> None:
    global _http
    if _http is not None:
        await _http.close()
        _http = None
