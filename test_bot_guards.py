#!/usr/bin/env python3
"""Регрессионные тесты парсеров (python test_bot_guards.py)."""

from crypto_bot.clients.dw.base import _row
from crypto_bot.core import guards
from crypto_bot.domain.ticker import parse_ticker
from crypto_bot.models.market import ExchangeSnapshot, MarketTicker, NetworkWallet, WalletStatus
from crypto_bot.services.formatter import format_report


def test_codes() -> None:
    assert guards.ret_ok(0) and guards.ret_ok("0")
    assert guards.okx_ok(0) and guards.okx_ok("0")
    assert guards.kucoin_ok(200000) and guards.kucoin_ok("200000")
    assert guards.code_ok(0, 0, "0")
    assert not guards.ret_ok(1)


def test_spot_price_helpers() -> None:
    assert guards.price_positive("1.05") == 1.05
    assert guards.price_positive(0) is None
    assert guards.dict_field_price({"price": "2"}, "price") == 2.0
    assert guards.list_row_price([{"last": "3.1"}], "last") == 3.1


def test_ticker_parser() -> None:
    assert parse_ticker("sol") == ("SOL", "USDT")
    assert parse_ticker("BTCUSDT") == ("BTC", "USDT")


def test_report_layout() -> None:
    snaps = [
        ExchangeSnapshot(
            key="binance",
            name="Binance",
            spot=MarketTicker(
                price=0.0658,
                url="https://www.binance.com/en/trade/MIRA_USDT",
            ),
            futures=MarketTicker(
                price=0.0658,
                funding_rate=0.00005,
                url="https://www.binance.com/en/futures/MIRAUSDT",
            ),
            wallet=WalletStatus(
                networks=[NetworkWallet(network="BSC", deposit=True, withdraw=True)]
            ),
        ),
        ExchangeSnapshot(
            key="bitget",
            name="Bitget",
            spot=MarketTicker(price=0.06585),
            futures=MarketTicker(price=0.06585),
        ),
    ]
    text = format_report("MIRA", "USDT", snaps, [])
    assert "<code>MIRA</code>" in text
    assert "Exchanges" in text and "Binance" in text
    assert "Futures</b></a>" in text
    assert "trade/MIRA_USDT" in text
    assert "Binance</b></a>" in text
    assert "✅" in text
    assert "🔎" not in text and "💰 Цена:" not in text


def test_dw_row() -> None:
    row = _row(
        "BEP20",
        coin="MIRA",
        deposit="1",
        withdraw="0",
        min_withdraw="0.001",
        withdraw_fee="0.0005",
    )
    assert row is not None
    assert row.network == "BSC"
    assert row.deposit is True
    assert row.withdraw is False
    assert row.min_withdraw == "0.001"
    assert row.withdraw_fee == "0.0005"


def test_dex_symbols() -> None:
    from crypto_bot.domain.exchange_symbols import _fallback_symbol

    assert _fallback_symbol("hyperliquid", "futures", "BTC", "USDT") == "BTC"
    assert _fallback_symbol("aster", "spot", "BTC", "USDT") == "BTCUSDT"


def test_float_field() -> None:
    assert guards.float_field("0.0000125") == 0.0000125
    assert guards.float_field("0") == 0.0
    assert guards.float_field("") is None


def test_futures_only_line() -> None:
    from crypto_bot.services.formatter import _exchange_line

    snap = ExchangeSnapshot(
        key="binance",
        name="Binance",
        futures=MarketTicker(
            price=0.0151,
            url="https://www.binance.com/en/futures/PIPPINUSDT",
        ),
    )
    line = _exchange_line(snap)
    assert "Futures</b></a>" in line
    assert "<b>Binance</b>" in line
    assert "Binance</b></a>" not in line
    assert "futures/PIPPIN" in line


def test_snapshot_coalesce() -> None:
    from crypto_bot.services.snapshot_merge import coalesce_snapshot

    prev = ExchangeSnapshot(
        key="gate",
        name="Gate.io",
        spot=MarketTicker(price=1.0),
        futures=MarketTicker(price=1.01, funding_rate=0.0001),
    )
    empty = ExchangeSnapshot(key="gate", name="Gate.io")
    assert coalesce_snapshot(prev, empty) is prev

    partial = ExchangeSnapshot(
        key="gate",
        name="Gate.io",
        futures=MarketTicker(price=1.02),
    )
    merged = coalesce_snapshot(prev, partial)
    assert merged.spot is not None and merged.spot.price == 1.0
    assert merged.futures is not None and merged.futures.price == 1.02
    assert merged.futures.funding_rate == 0.0001


def test_cmc_resolve_prefers_multi_exchange_listing() -> None:
    from crypto_bot.clients.coinmarketcap import CoinMarketCapClient
    from crypto_bot.models.market import ContractInfo

    rows = [
        ("binance", "ETH", "0x419d0d8bdd9af5e606ae2232ed285aff190e711b"),
        ("bitget", "BASE", "0x16ee7ecac70d1028e7712751e2ee6ba808a7dd92"),
        ("gate", "BASE", "0x16ee7ecac70d1028e7712751e2ee6ba808a7dd92"),
    ]
    old = CoinMarketCapClient._exchange_contract_score(
        {"is_active": True, "rank": 806},
        rows,
        [ContractInfo(network="ETH", address="0x419d0d8bdd9af5e606ae2232ed285aff190e711b")],
    )
    meme = CoinMarketCapClient._exchange_contract_score(
        {"is_active": True, "rank": 971},
        rows,
        [
            ContractInfo(network="SOL", address="8cn4JeRLiHTtfX6maZAsipGGyyZPdEcos3s2X3Hw3BS6"),
            ContractInfo(network="BASE", address="0x16ee7ecac70d1028e7712751e2ee6ba808a7dd92"),
        ],
    )
    assert meme > old


def test_coingecko_solana_contract() -> None:
    detail = {
        "detail_platforms": {
            "solana": {
                "contract_address": "So11111111111111111111111111111111111111112"
            },
            "base": {
                "contract_address": "0x16ee7ecac70d1028e7712751e2ee6ba808a7dd92"
            },
        }
    }
    out: list = []
    for pid, info in detail["detail_platforms"].items():
        from crypto_bot.clients.coinmarketcap import filter_display_contracts, is_evm, is_solana, norm_chain
        from crypto_bot.models.market import ContractInfo

        addr = info["contract_address"]
        net = norm_chain(pid)
        if is_evm(addr):
            out.append(ContractInfo(network=net, address=addr))
        elif net == "SOL" and is_solana(addr):
            out.append(ContractInfo(network="SOL", address=addr))
    shown = filter_display_contracts(out, coin="FUN")
    nets = {c.network for c in shown}
    assert "SOL" in nets
    assert "BASE" in nets


def test_hide_native_l1_contract() -> None:
    from crypto_bot.clients.coinmarketcap import filter_display_contracts, is_native_chain_contract
    from crypto_bot.models.market import ContractInfo

    assert is_native_chain_contract("SOL", "SOLANA")
    assert is_native_chain_contract("BTC", "BITCOIN")
    sol_mint = "So11111111111111111111111111111111111111112"
    wrapped = [
        ContractInfo(network="SOL", address=sol_mint),
        ContractInfo(network="BSC", address="0x570a5d26f7765ecb712c0924e4de545b89fd43df"),
    ]
    shown = filter_display_contracts(wrapped, coin="SOL")
    nets = {c.network for c in shown}
    assert "SOL" not in nets
    assert "BSC" in nets


def test_network_registry() -> None:
    from crypto_bot.domain.network_registry import is_token_ticker, resolve_network

    assert resolve_network("BEP20", coin="MIRA") == "BSC"
    assert resolve_network("SOL", coin="MIRA") == "SOL"
    assert resolve_network("MIRA", coin="MIRA") is None
    assert resolve_network("BTC", coin="BTC") == "BTC"
    assert resolve_network("BITCOIN", coin="BTC") == "BTC"
    assert resolve_network("STX", coin="STX") == "STX"
    assert resolve_network("STACKS", coin="STX") == "STX"
    assert is_token_ticker("MIRA", "MIRA")
    assert resolve_network("OPTIMISM", coin="OP") == "OPTIMISM"
    assert resolve_network("SOL", coin="OP") == "SOL"


if __name__ == "__main__":
    test_codes()
    test_spot_price_helpers()
    test_ticker_parser()
    test_dw_row()
    test_coingecko_solana_contract()
    test_futures_only_line()
    test_snapshot_coalesce()
    test_hide_native_l1_contract()
    test_network_registry()
    test_dex_symbols()
    test_float_field()
    test_report_layout()
    print("OK")
