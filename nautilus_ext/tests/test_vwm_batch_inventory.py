from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from data_engine.historical.catalog import partition_dir
from scripts.run_vwm_batch_backtests import scan_vwm_inventory
from scripts.run_vwm_batch_backtests import scan_native_catalog
from scripts.run_vwm_batch_backtests import write_native_catalog_outputs
from scripts.run_vwm_batch_backtests import write_inventory_outputs


pa = pytest.importorskip("pyarrow")
pq = pytest.importorskip("pyarrow.parquet")


def _write_bar_partition(root: Path, *, symbol: str, date: str, bar_type: str = "1m", missing_volume: bool = False) -> None:
    pdir = partition_dir(
        root,
        exchange="BINANCE",
        venue_type="spot",
        symbol=symbol,
        data_kind="bar",
        bar_type=bar_type,
        date=date,
    )
    pdir.mkdir(parents=True, exist_ok=True)
    data = {
        "ts": pa.array([1_000_000, 2_000_000], pa.timestamp("us")),
        "open": [100.0, 101.0],
        "high": [102.0, 103.0],
        "low": [99.0, 100.0],
        "close": [101.0, 102.0],
    }
    if not missing_volume:
        data["volume"] = [10.0, 11.0]
    pq.write_table(pa.table(data), pdir / "part-0.parquet")


def test_inventory_groups_dates_rows_and_schema(tmp_path):
    root = tmp_path / "historical_data" / "market_data"
    _write_bar_partition(root, symbol="BTCUSDT", date="2026-06-10")
    _write_bar_partition(root, symbol="BTCUSDT", date="2026-06-11")
    rows = scan_vwm_inventory(root)
    assert len(rows) == 1
    row = rows[0]
    assert row.exchange == "BINANCE"
    assert row.venue_type == "spot"
    assert row.symbol == "BTCUSDT"
    assert row.bar_type == "1m"
    assert row.first_date == "2026-06-10"
    assert row.last_date == "2026-06-11"
    assert row.num_partitions == 2
    assert row.estimated_rows == 4
    assert row.required_columns_present is True
    assert row.status == "usable"


def test_inventory_marks_missing_columns_and_incomplete(tmp_path):
    root = tmp_path / "historical_data" / "market_data"
    _write_bar_partition(root, symbol="ETHUSDT", date="2026-06-10")
    _write_bar_partition(root, symbol="ETHUSDT", date="2026-06-12")
    _write_bar_partition(root, symbol="BROKEN", date="2026-06-10", missing_volume=True)
    _write_bar_partition(root, symbol="BROKEN", date="2026-06-11", missing_volume=True)
    rows = {r.symbol: r for r in scan_vwm_inventory(root)}
    assert rows["ETHUSDT"].status == "incomplete"
    assert "date gaps" in rows["ETHUSDT"].notes
    assert rows["BROKEN"].status == "missing_columns"
    assert "volume" in rows["BROKEN"].notes


def test_inventory_outputs_stay_under_backtest_inventory(tmp_path):
    root = tmp_path / "historical_data" / "market_data"
    _write_bar_partition(root, symbol="BTCUSDT", date="2026-06-10")
    _write_bar_partition(root, symbol="BTCUSDT", date="2026-06-11")
    out = tmp_path / "outputs" / "backtest_inventory"
    paths = write_inventory_outputs(scan_vwm_inventory(root), out)
    assert Path(paths["csv"]).is_file()
    assert Path(paths["json"]).is_file()
    assert Path(paths["md"]).is_file()
    with open(paths["csv"], newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    assert rows[0]["symbol"] == "BTCUSDT"
    assert json.loads(Path(paths["json"]).read_text(encoding="utf-8"))[0]["status"] == "usable"


def test_inventory_rejects_output_outside_allowed_roots(tmp_path):
    with pytest.raises(ValueError, match="outputs/backtests"):
        write_inventory_outputs([], tmp_path / "elsewhere")


def test_native_catalog_inventory_marks_quote_tick_not_bar_candidate(tmp_path):
    root = tmp_path / "nautilus_catalog"
    pdir = root / "cffex_l1_quote" / "data" / "quote_tick" / "IH2303.CFFEX"
    pdir.mkdir(parents=True)
    table = pa.table(
        {
            "ts_event": pa.array([1, 2], pa.uint64()),
            "bid_price": [3800.0, 3800.2],
            "ask_price": [3800.4, 3800.6],
            "bid_size": [1.0, 2.0],
            "ask_size": [1.0, 2.0],
        }
    )
    pq.write_table(
        table,
        pdir / "2023-01-03T01-29-00-200000000Z_2023-01-03T06-59-59-200000000Z.parquet",
    )
    rows = scan_native_catalog(root)
    assert len(rows) == 1
    row = rows[0]
    assert row.catalog == "cffex_l1_quote"
    assert row.data_type == "quote_tick"
    assert row.symbol == "IH2303.CFFEX"
    assert row.estimated_rows == 2
    assert row.vwm_bar_candidate is False
    assert row.status == "not_bar_data"
    assert "not directly OHLCV bars" in row.notes

    out = tmp_path / "outputs" / "backtest_inventory"
    paths = write_native_catalog_outputs(rows, out)
    assert Path(paths["csv"]).is_file()
    assert Path(paths["json"]).is_file()
    assert Path(paths["md"]).is_file()
