"""Application settings (pydantic-settings v2)."""

from __future__ import annotations

from functools import lru_cache
from typing import Self

import os

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Environment-backed configuration."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    bot_token: SecretStr = Field(alias="BOT_TOKEN")
    default_quote: str = Field(default="USDT", alias="DEFAULT_QUOTE")
    cmc_api_key: SecretStr | None = Field(default=None, alias="CMC_API_KEY")

    binance_api_key: SecretStr | None = Field(default=None, alias="BINANCE_API_KEY")
    binance_api_secret: SecretStr | None = Field(default=None, alias="BINANCE_API_SECRET")
    bybit_api_key: SecretStr | None = Field(default=None, alias="BYBIT_API_KEY")
    bybit_api_secret: SecretStr | None = Field(default=None, alias="BYBIT_API_SECRET")
    okx_api_key: SecretStr | None = Field(default=None, alias="OKX_API_KEY")
    okx_api_secret: SecretStr | None = Field(default=None, alias="OKX_API_SECRET")
    okx_api_passphrase: SecretStr | None = Field(default=None, alias="OKX_API_PASSPHRASE")
    mexc_api_key: SecretStr | None = Field(default=None, alias="MEXC_API_KEY")
    mexc_api_secret: SecretStr | None = Field(default=None, alias="MEXC_API_SECRET")
    bingx_api_key: SecretStr | None = Field(default=None, alias="BINGX_API_KEY")
    bingx_api_secret: SecretStr | None = Field(default=None, alias="BINGX_API_SECRET")
    bitget_api_key: SecretStr | None = Field(default=None, alias="BITGET_API_KEY")
    bitget_api_secret: SecretStr | None = Field(default=None, alias="BITGET_API_SECRET")
    bitget_api_passphrase: SecretStr | None = Field(default=None, alias="BITGET_API_PASSPHRASE")
    kucoin_api_key: SecretStr | None = Field(default=None, alias="KUCOIN_API_KEY")
    kucoin_api_secret: SecretStr | None = Field(default=None, alias="KUCOIN_API_SECRET")
    kucoin_api_passphrase: SecretStr | None = Field(default=None, alias="KUCOIN_API_PASSPHRASE")
    kraken_api_key: SecretStr | None = Field(default=None, alias="KRAKEN_API_KEY")
    kraken_api_secret: SecretStr | None = Field(default=None, alias="KRAKEN_API_SECRET")

    log_level: str = Field(default="INFO", alias="LOG_LEVEL")
    http_timeout: float = Field(default=0.55, alias="HTTP_TIMEOUT")
    http_max_retries: int = Field(default=2, alias="HTTP_MAX_RETRIES")
    report_cache_ttl: int = Field(default=75, alias="REPORT_CACHE_TTL")
    report_cache_stale_ttl: int = Field(default=120, alias="REPORT_CACHE_STALE_TTL")
    fast_cache_ttl: int = Field(default=75, alias="FAST_CACHE_TTL")
    fast_wait_ms: int = Field(default=320, alias="FAST_WAIT_MS")
    dw_cache_ttl: int = Field(default=600, alias="DW_CACHE_TTL")
    turbo_mode: bool = Field(default=False, alias="TURBO_MODE")
    asia_vps: bool = Field(default=False, alias="ASIA_VPS")
    enrich_split: bool = Field(default=True, alias="ENRICH_SPLIT")
    first_response_sec: float = Field(default=0.9, alias="FIRST_RESPONSE_SEC")
    first_paint_ms: int = Field(default=300, alias="FIRST_PAINT_MS")
    http_connect_timeout: float = Field(default=0.28, alias="HTTP_CONNECT_TIMEOUT")
    http_proxy: str | None = Field(default=None, alias="HTTP_PROXY")

    @field_validator("turbo_mode", "asia_vps", "enrich_split", mode="before")
    @classmethod
    def _boolish(cls, value: object) -> bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.strip().lower() in ("1", "true", "yes", "on")
        return bool(value)

    @model_validator(mode="after")
    def _asia_profile(self) -> Self:
        """Vultr Tokyo / JP proxy: enable turbo stack when ASIA_VPS=1."""
        if self.asia_vps and not self.turbo_mode:
            self.turbo_mode = True
        return self

    @field_validator("default_quote")
    @classmethod
    def _upper_quote(cls, value: str) -> str:
        return value.strip().upper() or "USDT"

    @field_validator("bot_token")
    @classmethod
    def _validate_bot_token(cls, value: SecretStr) -> SecretStr:
        token = value.get_secret_value().strip().strip('"').strip("'")
        upper = token.upper()
        if (
            not token
            or len(token) < 30
            or "ВСТАВЬ" in upper
            or ("YOUR" in upper and ":" not in token)
        ):
            msg = (
                "Invalid BOT_TOKEN in .env — get it from @BotFather and set "
                "BOT_TOKEN=123456789:ABC..."
            )
            raise ValueError(msg)
        return SecretStr(token)

    def credentials_for(self, exchange_key: str) -> dict[str, str]:
        """CCXT credentials for authenticated wallet endpoints."""
        mapping: dict[str, tuple[SecretStr | None, SecretStr | None, SecretStr | None]] = {
            "binance": (self.binance_api_key, self.binance_api_secret, None),
            "bybit": (self.bybit_api_key, self.bybit_api_secret, None),
            "okx": (self.okx_api_key, self.okx_api_secret, self.okx_api_passphrase),
            "mexc": (self.mexc_api_key, self.mexc_api_secret, None),
            "bingx": (self.bingx_api_key, self.bingx_api_secret, None),
            "bitget": (self.bitget_api_key, self.bitget_api_secret, self.bitget_api_passphrase),
            "kucoin": (self.kucoin_api_key, self.kucoin_api_secret, self.kucoin_api_passphrase),
            "kraken": (self.kraken_api_key, self.kraken_api_secret, None),
        }
        keys = mapping.get(exchange_key)
        if not keys:
            return {}
        api_key, secret, password = keys
        if not api_key or not secret:
            return {}
        out: dict[str, str] = {
            "apiKey": api_key.get_secret_value(),
            "secret": secret.get_secret_value(),
        }
        if password:
            out["password"] = password.get_secret_value()
        return out

    def has_auth(self, exchange_key: str) -> bool:
        return bool(self.credentials_for(exchange_key))

    def cmc_key(self) -> str | None:
        if self.cmc_api_key is None:
            return None
        return self.cmc_api_key.get_secret_value() or None

    def outbound_proxy(self) -> str | None:
        return (
            (self.http_proxy or "").strip()
            or os.environ.get("HTTPS_PROXY")
            or os.environ.get("HTTP_PROXY")
            or None
        )

    @classmethod
    @lru_cache(maxsize=1)
    def load(cls) -> Self:
        return cls()


def get_settings() -> Settings:
    return Settings.load()
