"""Application settings (pydantic-settings v2)."""

from __future__ import annotations

from functools import lru_cache
from typing import Self

from pydantic import Field, SecretStr, field_validator
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

    log_level: str = Field(default="INFO", alias="LOG_LEVEL")
    http_timeout: float = Field(default=0.55, alias="HTTP_TIMEOUT")
    http_max_retries: int = Field(default=2, alias="HTTP_MAX_RETRIES")
    report_cache_ttl: int = Field(default=75, alias="REPORT_CACHE_TTL")
    report_cache_stale_ttl: int = Field(default=120, alias="REPORT_CACHE_STALE_TTL")
    fast_cache_ttl: int = Field(default=75, alias="FAST_CACHE_TTL")
    fast_wait_ms: int = Field(default=320, alias="FAST_WAIT_MS")
    dw_cache_ttl: int = Field(default=600, alias="DW_CACHE_TTL")

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

    @classmethod
    @lru_cache(maxsize=1)
    def load(cls) -> Self:
        return cls()


def get_settings() -> Settings:
    return Settings.load()
