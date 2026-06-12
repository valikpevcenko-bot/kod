"""Exchange registry and timing constants."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

BURST_EXCHANGE_KEYS: Final[frozenset[str]] = frozenset(
    {
        "binance",
        "bybit",
        "gate",
        "mexc",
        "bitget",
        "okx",
        "bingx",
        "kraken",
        "aster",
        "hyperliquid",
    }
)

FAST_TIER_KEYS: Final[frozenset[str]] = frozenset(
    {"binance", "bybit", "bitget", "mexc"}
)
FAST_TIER_MIN: Final[int] = 2
NOT_FOUND_GIVEUP: Final[float] = 0.35
NOT_FOUND_CACHE_TTL: Final[int] = 120

FAST_BURST_TIMEOUT: Final[float] = 0.48
FAST_BURST_MIN: Final[int] = 4
FAST_FETCH_TIMEOUT: Final[float] = 0.88
FETCH_HARD_TIMEOUT: Final[float] = 2.2

# TURBO_MODE / ASIA_VPS — progressive paint + enrich (Vultr JP / low RTT to CEX)
TURBO_FETCH_TIMEOUT: Final[float] = 0.82
TURBO_EXCHANGE_TIMEOUT_FAST: Final[float] = 0.44
TURBO_EXCHANGE_TIMEOUT_SLOW: Final[float] = 0.82
TURBO_FUNDING_TIMEOUT: Final[float] = 0.55
TURBO_BACKFILL_TIMEOUT: Final[float] = 0.16
TURBO_ENRICH_PRICES_TIMEOUT: Final[float] = 2.2
TURBO_WALLET_PHASE_TIMEOUT: Final[float] = 5.5
TURBO_PRICE_CONTINUATION_TIMEOUT: Final[float] = 2.6
LOADING_FOOTER_MIN_EXCHANGES: Final[int] = 6

ENRICH_PRICES_TIMEOUT: Final[float] = 7.5
ENRICH_RETRY_TIMEOUT: Final[float] = 4.0
ENRICH_JOB_TIMEOUT: Final[float] = 42.0
ENRICH_WALLET_PHASE_TIMEOUT: Final[float] = 12.0

PER_EXCHANGE_TIMEOUT: Final[float] = 2.4
EXCHANGE_PRICE_TIMEOUT: Final[dict[str, float]] = {
    "gate": 8.5,
    "mexc": 3.5,
    "kraken": 3.0,
    "kucoin": 3.2,
    "okx": 2.8,
    "aster": 3.5,
    "hyperliquid": 3.0,
}
FAST_EXCHANGE_TIMEOUT: Final[float] = 0.7

PRICE_PARALLEL_TIMEOUT: Final[float] = 3.2
PRICE_GRACE_TIMEOUT: Final[float] = 0.4

FUNDING_CACHE_TTL: Final[int] = 60
FUNDING_FETCH_TIMEOUT: Final[float] = 2.5
FUNDING_BATCH_TIMEOUT: Final[float] = 4.2

CONTRACT_HTTP_TIMEOUT: Final[float] = 2.8
WALLET_CALL_TIMEOUT: Final[float] = 12.0
AUTH_WALLET_TIMEOUT: Final[float] = 5.0

RETRY_EXCHANGE_PRIORITY: Final[tuple[str, ...]] = (
    "gate",
    "mexc",
    "kucoin",
    "kraken",
    "okx",
    "bingx",
    "aster",
    "hyperliquid",
)

DEX_EXCHANGE_KEYS: Final[frozenset[str]] = frozenset({"aster", "hyperliquid"})

DISPLAY_CONTRACT_NETWORKS: Final[frozenset[str]] = frozenset(
    {
        "BSC",
        "ETH",
        "SOL",
        "BASE",
        "ARBITRUM",
        "OPTIMISM",
        "POLYGON",
        "MANTLE",
        "STARKNET",
        "ZKSYNCERA",
        "LINEA",
        "SCROLL",
        "CYBER",
        "WLD",
    }
)


@dataclass(frozen=True, slots=True)
class ExchangeDef:
    key: str
    name: str
    futures_only: bool = False


EXCHANGE_DEFS: list[ExchangeDef] = [
    ExchangeDef("binance", "Binance"),
    ExchangeDef("bybit", "Bybit"),
    ExchangeDef("gate", "Gate.io"),
    ExchangeDef("mexc", "MEXC"),
    ExchangeDef("bitget", "Bitget"),
    ExchangeDef("okx", "OKX"),
    ExchangeDef("kucoin", "KuCoin"),
    ExchangeDef("bingx", "BingX"),
    ExchangeDef("kraken", "Kraken"),
    ExchangeDef("aster", "AsterDEX"),
    ExchangeDef("hyperliquid", "Hyperliquid", futures_only=True),
]

EXCHANGE_BY_KEY: dict[str, ExchangeDef] = {d.key: d for d in EXCHANGE_DEFS}
SPOT_EXCHANGE_DEFS: tuple[ExchangeDef, ...] = tuple(d for d in EXCHANGE_DEFS if not d.futures_only)
REPORT_EXCHANGE_COUNT: Final[int] = len(EXCHANGE_DEFS)
