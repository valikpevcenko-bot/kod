"""Token contract addresses from exchanges + CMC."""

from __future__ import annotations

import asyncio
import time
from typing import Any

import ccxt.async_support as ccxt
import structlog

from crypto_bot.clients.coinmarketcap import (
    CoinMarketCapClient,
    filter_display_contracts,
    is_evm,
    is_solana,
    norm_chain,
)
from crypto_bot.config.settings import get_settings
from crypto_bot.core import guards
from crypto_bot.core.http import get_http
from crypto_bot.domain.exchanges import CONTRACT_HTTP_TIMEOUT
from crypto_bot.models.market import ContractInfo

logger = structlog.get_logger(__name__)

_CACHE: dict[str, tuple[float, list[ContractInfo]]] = {}
_MEM: dict[str, tuple[float, list[ContractInfo]]] = {}
_CACHE_TTL = 600
_MEM_TTL = 600
_binance_coins: tuple[float, list] | None = None

_CCXT_EXCHANGE = {
    "bybit": ("bybit", None),
    "okx": ("okx", {"options": {"defaultType": "spot"}}),
    "mexc": ("mexc", None),
    "bingx": ("bingx", None),
}


class ContractService:
    """Aggregate contract addresses across exchanges."""

    def __init__(self) -> None:
        self._http = get_http()
        self._cmc = CoinMarketCapClient()
        self._settings = get_settings()

    def cached(self, symbol: str) -> list[ContractInfo]:
        sym = symbol.upper()
        item = _MEM.get(sym)
        if item and time.time() - item[0] <= _MEM_TTL:
            return item[1]
        hit = _CACHE.get(sym)
        if hit and time.time() - hit[0] < _CACHE_TTL:
            return hit[1]
        return []

    def store(self, symbol: str, contracts: list[ContractInfo]) -> None:
        if contracts:
            sym = symbol.upper()
            _MEM[sym] = (time.time(), contracts)
            _CACHE[sym] = (time.time(), contracts)

    async def preload_binance(self) -> None:
        await self._binance_rows("BTC")

    async def fetch(
        self,
        symbol: str,
        listed_on: list[str] | None = None,
    ) -> list[ContractInfo]:
        key = symbol.upper()
        hit = _CACHE.get(key)
        if hit and time.time() - hit[0] < _CACHE_TTL:
            return hit[1]
        mem = self.cached(key)
        if mem:
            return mem

        exchanges = listed_on or list(_FETCHERS) + list(_CCXT_EXCHANGE)
        has_cmc = bool(self._settings.cmc_key())
        cmc_task = asyncio.create_task(self._cmc.map_entries(key)) if has_cmc else None

        tasks: list[tuple[str, asyncio.Task]] = []
        for ex_key in exchanges:
            if ex_key in _FETCHERS:
                tasks.append((ex_key, asyncio.create_task(_FETCHERS[ex_key](self, key))))
            elif ex_key in _CCXT_EXCHANGE:
                tasks.append((ex_key, asyncio.create_task(self._ccxt_rows(ex_key, key))))

        all_rows: list[tuple[str, str, str]] = []
        if tasks:
            results = await asyncio.gather(*[t for _, t in tasks], return_exceptions=True)
            for (ex_key, _), result in zip(tasks, results):
                if isinstance(result, list):
                    all_rows.extend(result)

        cmc_id: int | None = None
        if cmc_task:
            try:
                entries = await cmc_task
                cmc_id = await self._cmc.resolve_cmc_id(entries, all_rows)
            except Exception:
                pass

        consensus = self._cmc.consensus(all_rows)
        if has_cmc and cmc_id:
            cmc_contracts = await self._cmc.contracts_by_id(cmc_id)
            if cmc_contracts:
                by_net = {c.network: c for c in consensus}
                for c in cmc_contracts:
                    by_net[c.network] = c
                consensus = list(by_net.values())
                from crypto_bot.clients.coinmarketcap import NETWORK_ORDER

                consensus.sort(
                    key=lambda x: (NETWORK_ORDER.get(x.network, 99), x.network)
                )
        elif not consensus:
            try:
                consensus = await self._coingecko_fallback(key)
            except Exception:
                logger.debug("coingecko_contracts_skip", symbol=key)

        consensus = filter_display_contracts(consensus, coin=key)

        self.store(key, consensus)
        _CACHE[key] = (time.time(), consensus)
        return consensus

    async def _binance_rows(self, symbol: str) -> list[tuple[str, str, str]]:
        global _binance_coins
        now = time.time()
        if not _binance_coins or now - _binance_coins[0] > _CACHE_TTL:
            data = await self._http.get_json(
                "https://www.binance.com/bapi/capital/v1/public/capital/getNetworkCoinAll",
                timeout=CONTRACT_HTTP_TIMEOUT,
            )
            coins = (data or {}).get("data") or [] if isinstance(data, dict) else []
            _binance_coins = (time.time(), coins)
        rows: list[tuple[str, str, str]] = []
        for item in _binance_coins[1]:
            if str(item.get("coin", "")).upper() != symbol:
                continue
            for net in item.get("networkList") or []:
                label = norm_chain(net.get("network") or net.get("name") or "")
                addr = (net.get("contractAddress") or "").strip() or "native"
                rows.append(("binance", label, addr))
            return rows
        return []

    async def _bitget_rows(self, symbol: str) -> list[tuple[str, str, str]]:
        data = await self._http.get_json(
            "https://api.bitget.com/api/v2/spot/public/coins",
            params={"coin": symbol},
        )
        if not isinstance(data, dict):
            return []
        rows: list[tuple[str, str, str]] = []
        for item in data.get("data") or []:
            for ch in item.get("chains") or []:
                label = norm_chain(ch.get("chain") or "")
                addr = (ch.get("contractAddress") or "").strip() or "native"
                rows.append(("bitget", label, addr))
        return rows

    async def _gate_rows(self, symbol: str) -> list[tuple[str, str, str]]:
        data = await self._http.get_json(
            "https://api.gateio.ws/api/v4/wallet/currency_chains",
            params={"currency": symbol},
        )
        if not isinstance(data, list):
            return []
        return [
            (
                "gate",
                norm_chain(ch.get("chain") or ch.get("name_en") or ""),
                (ch.get("contract_address") or "").strip() or "native",
            )
            for ch in data
        ]

    async def _kucoin_rows(self, symbol: str) -> list[tuple[str, str, str]]:
        data = await self._http.get_json(f"https://api.kucoin.com/api/v2/currencies/{symbol}")
        if not isinstance(data, dict) or not guards.kucoin_ok(data.get("code")):
            return []
        row = data.get("data") or {}
        return [
            (
                "kucoin",
                norm_chain(ch.get("chainName") or ch.get("chain") or ""),
                (ch.get("contractAddress") or "").strip() or "native",
            )
            for ch in row.get("chains") or []
        ]

    async def _ccxt_rows(self, exchange_key: str, symbol: str) -> list[tuple[str, str, str]]:
        cfg = _CCXT_EXCHANGE.get(exchange_key)
        if not cfg:
            return []
        try:
            klass = getattr(ccxt, cfg[0])
            params: dict[str, Any] = {"enableRateLimit": True, "timeout": 15000}
            if cfg[1]:
                params.update(cfg[1])
            ex = klass(params)
            currencies = await asyncio.wait_for(ex.fetch_currencies(), timeout=20)
            await ex.close()
        except Exception as exc:
            logger.debug("ccxt_contracts", exchange=exchange_key, error=str(exc)[:80])
            return []
        cur = currencies.get(symbol) or currencies.get(symbol.upper())
        if not cur:
            return []
        rows: list[tuple[str, str, str]] = []
        for net_id, net in (cur.get("networks") or {}).items():
            if not isinstance(net, dict):
                continue
            label = norm_chain(net.get("network") or net_id)
            info = net.get("info") or {}
            addr = (info.get("contractAddress") or info.get("contract") or "").strip()
            if not addr:
                if net.get("deposit") is not None or net.get("withdraw") is not None:
                    rows.append((exchange_key, label, "native"))
                continue
            rows.append((exchange_key, label, addr))
        return rows

    async def _coingecko_fallback(self, symbol: str) -> list[ContractInfo]:
        search = await self._http.get_json(
            "https://api.coingecko.com/api/v3/search",
            params={"query": symbol},
            timeout=3,
        )
        coin_id = None
        if isinstance(search, dict):
            for coin in search.get("coins") or []:
                if str(coin.get("symbol", "")).upper() == symbol:
                    coin_id = coin.get("id")
                    break
        if not coin_id:
            return []
        detail = await self._http.get_json(
            f"https://api.coingecko.com/api/v3/coins/{coin_id}",
            params={
                "localization": "false",
                "tickers": "false",
                "market_data": "false",
                "community_data": "false",
                "developer_data": "false",
            },
            timeout=4,
        )
        if not isinstance(detail, dict):
            return []
        out: list[ContractInfo] = []
        for pid, info in (detail.get("detail_platforms") or {}).items():
            addr = ((info or {}).get("contract_address") or "").strip()
            if not addr or addr.lower() in ("native", ""):
                continue
            net = norm_chain(str(pid))
            if is_evm(addr):
                out.append(ContractInfo(network=net, address=addr))
            elif net == "SOL" and is_solana(addr):
                out.append(ContractInfo(network="SOL", address=addr))
        return filter_display_contracts(out, coin=symbol)


_FETCHERS = {
    "binance": ContractService._binance_rows,
    "bitget": ContractService._bitget_rows,
    "gate": ContractService._gate_rows,
    "kucoin": ContractService._kucoin_rows,
}
