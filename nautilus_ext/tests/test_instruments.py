"""合约/标的元数据接入测试。

全部本地运行：不连真实交易所、不需要 API key、不在 import 期加载 ccxt、
不使用 pandas。
"""
from __future__ import annotations

import builtins
import importlib
import sys
from typing import Any

import pytest

from data_engine.instruments import (
    CcxtInstrumentProvider,
    InstrumentInfo,
    StaticInstrumentProvider,
    instrument_from_ccxt_market,
)

# ---- 样例 ccxt market dict（无网络） ----------------------------------------

SPOT_MARKET: dict[str, Any] = {
    "id": "BTCUSDT",
    "symbol": "BTC/USDT",
    "base": "BTC",
    "quote": "USDT",
    "settle": None,
    "type": "spot",
    "spot": True,
    "swap": False,
    "future": False,
    "option": False,
    "contract": False,
    "contractSize": None,
    "expiry": None,
    "precision": {"price": 2, "amount": 5},
    "limits": {
        "amount": {"min": 0.00001, "max": 9000.0},
        "price": {"min": 0.01, "max": 1_000_000.0},
        "cost": {"min": 5.0, "max": None},
    },
    "active": True,
}

SWAP_MARKET: dict[str, Any] = {
    "id": "BTCUSDT",
    "symbol": "BTC/USDT:USDT",
    "base": "BTC",
    "quote": "USDT",
    "settle": "USDT",
    "type": "swap",
    "spot": False,
    "swap": True,
    "future": False,
    "option": False,
    "contract": True,
    "contractSize": 1.0,
    "expiry": None,
    "precision": {"price": 0.1, "amount": 0.001},  # TICK_SIZE 模式
    "limits": {
        "amount": {"min": 0.001, "max": 1000.0},
        "price": {"min": 0.1, "max": 1_000_000.0},
        "cost": {"min": 5.0, "max": None},
    },
    "active": True,
}

FUTURE_MARKET: dict[str, Any] = {
    "id": "BTCUSDT-20241227",
    "symbol": "BTC/USDT:USDT-20241227",
    "base": "BTC",
    "quote": "USDT",
    "settle": "USDT",
    "type": "future",
    "spot": False,
    "swap": False,
    "future": True,
    "option": False,
    "contract": True,
    "contractSize": 1.0,
    "expiry": 1735257600000,
    "precision": {"price": 1, "amount": 3},
    "limits": {
        "amount": {"min": 0.001, "max": 1000.0},
        "price": {"min": 0.1, "max": 1_000_000.0},
        "cost": {"min": 5.0, "max": None},
    },
    "active": True,
}

ALL_MARKETS = {
    "BTC/USDT": SPOT_MARKET,
    "BTC/USDT:USDT": SWAP_MARKET,
    "BTC/USDT:USDT-20241227": FUTURE_MARKET,
}


# ---- InstrumentInfo / StaticInstrumentProvider ------------------------------


def test_instrument_info_constructs() -> None:
    info = InstrumentInfo(
        instrument_id="BTC/USDT.binance",
        exchange="binance",
        symbol="BTC/USDT",
        market_type="spot",
    )
    assert info.instrument_id == "BTC/USDT.binance"
    assert info.raw == {}
    assert info.active is None


def test_static_provider_returns_instruments() -> None:
    infos = [
        InstrumentInfo("A.x", "x", "A", "spot"),
        InstrumentInfo("B.x", "x", "B", "swap"),
    ]
    prov = StaticInstrumentProvider(infos)
    got = prov.load_instruments()
    assert got == infos
    # 返回的是拷贝，外部修改不影响内部。
    got.clear()
    assert len(prov.load_instruments()) == 2


# ---- 归一化 -----------------------------------------------------------------


def test_normalize_spot_market() -> None:
    info = instrument_from_ccxt_market(SPOT_MARKET, "binance")
    assert info.symbol == "BTC/USDT"
    assert info.instrument_id == "BTC/USDT.binance"
    assert info.market_type == "spot"
    assert info.base == "BTC"
    assert info.quote == "USDT"
    assert info.price_precision == 2
    assert info.amount_precision == 5
    assert info.price_tick is None
    assert info.min_amount == 0.00001
    assert info.min_notional == 5.0
    assert info.expiry is None
    assert info.active is True
    assert info.raw["id"] == "BTCUSDT"


def test_normalize_swap_tick_size_mode() -> None:
    info = instrument_from_ccxt_market(SWAP_MARKET, "binance")
    assert info.market_type == "swap"
    assert info.settle == "USDT"
    assert info.contract_size == 1.0
    # TICK_SIZE 模式：precision 是步长而非位数。
    assert info.price_precision is None
    assert info.price_tick == 0.1
    assert info.amount_step == 0.001


def test_normalize_future_has_expiry() -> None:
    info = instrument_from_ccxt_market(FUTURE_MARKET, "binance")
    assert info.market_type == "future"
    assert info.expiry == 1735257600000


# ---- provider 归一化 + 过滤 -------------------------------------------------


def test_normalize_markets_all() -> None:
    prov = CcxtInstrumentProvider("binance")
    infos = prov._normalize_markets(ALL_MARKETS)
    assert len(infos) == 3


def test_symbol_filter() -> None:
    prov = CcxtInstrumentProvider("binance", symbols=["BTC/USDT"])
    infos = prov._normalize_markets(ALL_MARKETS)
    assert len(infos) == 1
    assert infos[0].symbol == "BTC/USDT"


def test_market_type_filter() -> None:
    prov = CcxtInstrumentProvider("binance", market_type="swap")
    infos = prov._normalize_markets(ALL_MARKETS)
    assert len(infos) == 1
    assert infos[0].market_type == "swap"


# ---- ccxt 懒加载 / 缺失处理 -------------------------------------------------


def test_ccxt_not_imported_at_module_import() -> None:
    sys.modules.pop("ccxt", None)
    import data_engine.instruments.ccxt_provider as m

    importlib.reload(m)
    # 仅 import provider 模块不应把 ccxt 拉进来。
    assert "ccxt" not in sys.modules


def test_missing_ccxt_raises_clear_importerror(monkeypatch) -> None:
    sys.modules.pop("ccxt", None)
    real_import = builtins.__import__

    def _fake_import(name, *args, **kwargs):
        if name == "ccxt":
            raise ImportError("simulated missing ccxt")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _fake_import)
    prov = CcxtInstrumentProvider("binance")
    with pytest.raises(ImportError, match="ccxt"):
        prov.load_instruments()


# ---- 持久化 -----------------------------------------------------------------


def test_instruments_to_polars() -> None:
    pl = pytest.importorskip("polars")
    from data_engine.instruments import instruments_to_polars

    infos = [instrument_from_ccxt_market(m, "binance") for m in ALL_MARKETS.values()]
    df = instruments_to_polars(infos)
    assert df.height == 3
    assert "instrument_id" in df.columns
    assert "raw_json" in df.columns
    assert "price_tick" in df.columns
    # raw_json 是字符串列。
    assert isinstance(df["raw_json"][0], str)


def test_instruments_to_polars_empty() -> None:
    pytest.importorskip("polars")
    from data_engine.instruments import instruments_to_polars

    df = instruments_to_polars([])
    assert df.height == 0
    assert "instrument_id" in df.columns


def test_write_instruments_parquet_hive_layout(tmp_path) -> None:
    pytest.importorskip("polars")
    ds = pytest.importorskip("pyarrow.dataset")
    from data_engine.instruments import write_instruments_parquet

    infos = [instrument_from_ccxt_market(m, "binance") for m in ALL_MARKETS.values()]
    root = tmp_path / "instruments"
    paths = write_instruments_parquet(
        infos, root, exchange="binance", as_of_date="2026-06-12"
    )
    assert paths
    # Hive 分区目录存在。
    part_dir = root / "exchange=binance" / "as_of_date=2026-06-12"
    assert part_dir.exists()
    # 读回校验。
    dataset = ds.dataset(str(root), format="parquet", partitioning="hive")
    table = dataset.to_table()
    assert table.num_rows == 3
    assert "exchange" in table.schema.names
    assert "as_of_date" in table.schema.names
