"""Tests for the historical data manager / local cache (data_engine.historical).

Pure-Python parts (catalog / plan / manifest / skip+failed download paths) run
without pyarrow; parquet read/write parts (validators, real download write) are
importorskip-gated.  No real Binance download (injected fetcher), no network,
no Nautilus.
"""
from __future__ import annotations

import json

import pytest

from data_engine.historical import (
    BinanceVisionHistoricalDownloader,
    LocalDataCatalog,
    Manifest,
    ManifestRecord,
    build_plan,
)
from data_engine.historical.catalog import partition_dir


# --------------------------------------------------------------------------
# helpers (no pyarrow): touch an empty placeholder partition
# --------------------------------------------------------------------------

def _touch_partition(root, *, data_kind, date, exchange="BINANCE", venue_type="spot",
                     symbol="BTCUSDT", bar_type=None, data_type=None):
    d = partition_dir(root, exchange=exchange, venue_type=venue_type, symbol=symbol,
                      data_kind=data_kind, bar_type=bar_type, data_type=data_type, date=date)
    d.mkdir(parents=True, exist_ok=True)
    (d / "part-0.parquet").write_bytes(b"")  # catalog counts files, not content
    return d


# --------------------------------------------------------------------------
# 1-2. inventory finds bar / trade partitions
# --------------------------------------------------------------------------

def test_inventory_finds_bar_partition(tmp_path):
    root = tmp_path / "market_data"
    _touch_partition(root, data_kind="bar", bar_type="5m", date="2024-06-01")
    parts = LocalDataCatalog(root).inventory()
    assert len(parts) == 1
    p = parts[0]
    assert p.data_kind == "bar" and p.bar_type == "5m" and p.data_type is None
    assert p.symbol == "BTCUSDT" and p.date == "2024-06-01" and p.file_count == 1


def test_inventory_finds_trade_partition(tmp_path):
    root = tmp_path / "market_data"
    _touch_partition(root, data_kind="trade", data_type="aggTrades", date="2024-06-01")
    parts = LocalDataCatalog(root).inventory()
    assert len(parts) == 1
    p = parts[0]
    assert p.data_kind == "trade" and p.data_type == "aggTrades" and p.bar_type is None


# --------------------------------------------------------------------------
# 3-5. plan existing/missing, skip-existing default, overwrite
# --------------------------------------------------------------------------

def test_plan_distinguishes_existing_and_missing(tmp_path):
    root = tmp_path / "market_data"
    _touch_partition(root, data_kind="bar", bar_type="5m", date="2024-06-01")
    plan = build_plan(root=str(root), exchange="BINANCE", venue_type="spot",
                      symbols="BTCUSDT", data_kind="bar", bar_type="5m",
                      start="2024-06-01", end="2024-06-03")
    assert len(plan.existing) == 1 and len(plan.missing) == 2
    assert {pp.date for pp in plan.missing} == {"2024-06-02", "2024-06-03"}


def test_skip_existing_is_default(tmp_path):
    root = tmp_path / "market_data"
    _touch_partition(root, data_kind="bar", bar_type="5m", date="2024-06-01")
    plan = build_plan(root=str(root), exchange="BINANCE", venue_type="spot",
                      symbols="BTCUSDT", data_kind="bar", bar_type="5m",
                      start="2024-06-01", end="2024-06-02", overwrite=False)
    assert [pp.date for pp in plan.skipped_existing] == ["2024-06-01"]
    assert [pp.date for pp in plan.planned_downloads] == ["2024-06-02"]


def test_overwrite_puts_existing_into_planned(tmp_path):
    root = tmp_path / "market_data"
    _touch_partition(root, data_kind="bar", bar_type="5m", date="2024-06-01")
    plan = build_plan(root=str(root), exchange="BINANCE", venue_type="spot",
                      symbols="BTCUSDT", data_kind="bar", bar_type="5m",
                      start="2024-06-01", end="2024-06-02", overwrite=True)
    assert {pp.date for pp in plan.planned_downloads} == {"2024-06-01", "2024-06-02"}
    assert plan.skipped_existing == []


# --------------------------------------------------------------------------
# 6-7. manifest append + location
# --------------------------------------------------------------------------

def test_manifest_append_jsonl(tmp_path):
    root = tmp_path / "market_data"
    m = Manifest(root)
    rec = m.append(ManifestRecord(status="downloaded", exchange="BINANCE", venue_type="spot",
                                  symbol="BTCUSDT", data_kind="bar", bar_type="5m",
                                  date="2024-06-01", row_count=288), now="2024-01-01T00:00:00")
    assert rec["status"] == "downloaded" and rec["created_at"] == "2024-01-01T00:00:00"
    lines = m.path.read_text().strip().split("\n")
    assert len(lines) == 1 and json.loads(lines[0])["row_count"] == 288
    assert m.read_all()[0]["symbol"] == "BTCUSDT"
    with pytest.raises(ValueError):
        m.append(ManifestRecord(status="bogus", exchange="x", venue_type="x", symbol="x",
                                data_kind="bar", date="d"))


def test_manifest_path_is_catalog_sibling(tmp_path):
    root = tmp_path / "market_data"
    m = Manifest(root)
    assert m.path.name == "manifest.jsonl"
    assert m.path.parent.name == "_catalog"
    # sibling of market_data, NOT inside it
    assert m.path.parent.parent == root.parent
    assert "market_data" not in m.path.parent.parts[-1]


# --------------------------------------------------------------------------
# 10. mixed root: inventory + find_partitions don't confuse bar/trade
# --------------------------------------------------------------------------

def test_mixed_root_inventory_distinguishes(tmp_path):
    root = tmp_path / "market_data"
    _touch_partition(root, data_kind="bar", bar_type="5m", date="2024-06-01")
    _touch_partition(root, data_kind="trade", data_type="aggTrades", date="2024-06-01")
    cat = LocalDataCatalog(root)
    assert len(cat.inventory()) == 2
    bars = cat.find_partitions(data_kind="bar")
    trades = cat.find_partitions(data_kind="trade")
    assert len(bars) == 1 and bars[0].bar_type == "5m"
    assert len(trades) == 1 and trades[0].data_type == "aggTrades"
    assert cat.partition_exists(exchange="BINANCE", venue_type="spot", symbol="BTCUSDT",
                                data_kind="bar", bar_type="5m", date="2024-06-01")
    assert not cat.partition_exists(exchange="BINANCE", venue_type="spot", symbol="BTCUSDT",
                                    data_kind="bar", bar_type="5m", date="2024-06-02")


# --------------------------------------------------------------------------
# 12-13. download skip / failed paths (no pyarrow, injected fetcher)
# --------------------------------------------------------------------------

def test_failed_download_records_failed(tmp_path):
    root = tmp_path / "market_data"

    def _raising_fetcher(**kwargs):
        raise RuntimeError("boom: simulated download failure")

    dl = BinanceVisionHistoricalDownloader(root, fetcher=_raising_fetcher)
    result, plan = dl.download(exchange="BINANCE", venue_type="spot", symbol="BTCUSDT",
                               data_kind="bar", bar_type="5m", start="2024-06-01",
                               end="2024-06-01", now="2024-01-01T00:00:00")
    assert len(result.failed) == 1 and not result.downloaded
    assert result.failed[0]["status"] == "failed"
    assert "boom" in result.failed[0]["error"]
    # no partition was created
    assert not LocalDataCatalog(root).partition_exists(
        exchange="BINANCE", venue_type="spot", symbol="BTCUSDT",
        data_kind="bar", bar_type="5m", date="2024-06-01")
    assert dl.manifest.read_all()[-1]["status"] == "failed"


def test_skipped_existing_records_skipped(tmp_path):
    root = tmp_path / "market_data"
    _touch_partition(root, data_kind="trade", data_type="aggTrades", date="2024-06-01")

    def _must_not_be_called(**kwargs):
        raise AssertionError("fetcher must not run for an existing partition")

    dl = BinanceVisionHistoricalDownloader(root, fetcher=_must_not_be_called)
    result, plan = dl.download(exchange="BINANCE", venue_type="spot", symbol="BTCUSDT",
                               data_kind="trade", data_type="aggTrades",
                               start="2024-06-01", end="2024-06-01", overwrite=False,
                               now="2024-01-01T00:00:00")
    assert len(result.skipped_existing) == 1 and not result.downloaded and not result.failed
    assert result.skipped_existing[0]["status"] == "skipped_existing"
    assert dl.manifest.read_all()[-1]["status"] == "skipped_existing"


# --------------------------------------------------------------------------
# 14. no nautilus_trader import in the historical package
# --------------------------------------------------------------------------

def test_no_nautilus_import_in_historical_package():
    import inspect

    import data_engine.historical as pkg
    from data_engine.historical import catalog, downloader, manifest, plan, validators

    for mod in (pkg, catalog, plan, manifest, validators, downloader):
        src = inspect.getsource(mod)
        assert "import nautilus_trader" not in src
        assert "from nautilus_trader" not in src


# ==========================================================================
# pyarrow-gated: validators + real download write
# ==========================================================================

def _write_bar_parquet(pdir, n=5):
    import pyarrow as pa
    import pyarrow.parquet as pq
    pdir.mkdir(parents=True, exist_ok=True)
    tbl = pa.table({
        "ts": pa.array([1_000_000_000 * i for i in range(n)], pa.int64()),
        "open": [100.0 + i for i in range(n)],
        "high": [101.0 + i for i in range(n)],
        "low": [99.0 + i for i in range(n)],
        "close": [100.5 + i for i in range(n)],
        "volume": [10.0 + i for i in range(n)],
        "instrument_id": ["BTCUSDT.BINANCE"] * n,
        "source": ["binance_vision"] * n,
    })
    pq.write_table(tbl, str(pdir / "part-0.parquet"))
    return n


def _write_trade_parquet(pdir, n=6):
    import pyarrow as pa
    import pyarrow.parquet as pq
    pdir.mkdir(parents=True, exist_ok=True)
    tbl = pa.table({
        "ts": pa.array([1_000_000_000 * i for i in range(n)], pa.int64()),
        "price": [100.0 + i for i in range(n)],
        "quantity": [1.0 + (i % 3) for i in range(n)],
        "quote_quantity": [(100.0 + i) * (1.0 + (i % 3)) for i in range(n)],
        "side": ["BUY" if i % 2 == 0 else "SELL" for i in range(n)],
        "is_buyer_maker": [i % 2 == 1 for i in range(n)],
        "agg_trade_id": list(range(n)),
        "instrument_id": ["BTCUSDT.BINANCE"] * n,
        "source": ["binance_vision_aggTrades"] * n,
    })
    pq.write_table(tbl, str(pdir / "part-0.parquet"))
    return n


def test_verify_bar_partition(tmp_path):
    pytest.importorskip("pyarrow")
    from data_engine.historical import validate_partition
    root = tmp_path / "market_data"
    pdir = partition_dir(root, exchange="BINANCE", venue_type="spot", symbol="BTCUSDT",
                         data_kind="bar", bar_type="5m", date="2024-06-01")
    n = _write_bar_parquet(pdir)
    res = validate_partition(root=root, exchange="BINANCE", venue_type="spot",
                             symbol="BTCUSDT", data_kind="bar", bar_type="5m", date="2024-06-01")
    assert res.ok and res.row_count == n
    assert "close_range" in res.details and "volume_range" in res.details


def test_verify_trade_partition(tmp_path):
    pytest.importorskip("pyarrow")
    from data_engine.historical import validate_partition
    root = tmp_path / "market_data"
    pdir = partition_dir(root, exchange="BINANCE", venue_type="spot", symbol="BTCUSDT",
                         data_kind="trade", data_type="aggTrades", date="2024-06-01")
    n = _write_trade_parquet(pdir)
    res = validate_partition(root=root, exchange="BINANCE", venue_type="spot",
                             symbol="BTCUSDT", data_kind="trade", data_type="aggTrades",
                             date="2024-06-01")
    assert res.ok and res.row_count == n
    assert res.details["duplicate_agg_trade_id"] == 0
    assert set(res.details["side_distribution"]) <= {"BUY", "SELL"}


def test_verify_missing_partition_fails(tmp_path):
    pytest.importorskip("pyarrow")
    from data_engine.historical import validate_partition
    root = tmp_path / "market_data"
    res = validate_partition(root=root, exchange="BINANCE", venue_type="spot",
                             symbol="BTCUSDT", data_kind="bar", bar_type="5m", date="2099-01-01")
    assert not res.ok and res.errors


def test_downloader_uses_mock_fetcher_no_network(tmp_path):
    pytest.importorskip("pyarrow")
    import pyarrow as pa

    def _bar_fetcher(**kwargs):
        assert kwargs["data_kind"] == "bar" and kwargs["symbol"] == "BTCUSDT"
        n = 3
        tbl = pa.table({
            "ts": pa.array([1_000_000_000 * i for i in range(n)], pa.int64()),
            "exchange": ["BINANCE"] * n, "venue_type": ["spot"] * n,
            "symbol": ["BTCUSDT"] * n, "instrument_id": ["BTCUSDT.BINANCE"] * n,
            "bar_type": ["5m"] * n,
            "open": [1.0, 2.0, 3.0], "high": [2.0, 3.0, 4.0],
            "low": [0.5, 1.0, 2.0], "close": [1.5, 2.5, 3.5],
            "volume": [10.0, 11.0, 12.0], "source": ["binance_vision"] * n,
        })
        return tbl, "http://mock/BTCUSDT-5m-2024-06-02.zip"

    root = tmp_path / "market_data"
    dl = BinanceVisionHistoricalDownloader(root, fetcher=_bar_fetcher)
    result, plan = dl.download(exchange="BINANCE", venue_type="spot", symbol="BTCUSDT",
                               data_kind="bar", bar_type="5m", start="2024-06-02",
                               end="2024-06-02", now="2024-01-01T00:00:00")
    assert len(result.downloaded) == 1 and not result.failed
    rec = result.downloaded[0]
    assert rec["status"] == "downloaded" and rec["row_count"] == 3
    assert rec["source_url"].endswith(".zip")
    # the partition now exists and re-planning skips it
    cat = LocalDataCatalog(root)
    assert cat.partition_exists(exchange="BINANCE", venue_type="spot", symbol="BTCUSDT",
                                data_kind="bar", bar_type="5m", date="2024-06-02")
