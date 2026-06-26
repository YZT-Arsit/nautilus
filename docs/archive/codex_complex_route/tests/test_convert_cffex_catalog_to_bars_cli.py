from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import inspect

import pytest

import scripts.convert_cffex_catalog_to_bars as cli
from scripts.convert_cffex_catalog_to_bars import build_plan
from scripts.convert_cffex_catalog_to_bars import main
from scripts.convert_cffex_catalog_to_bars import safe_output_root


pa = pytest.importorskip("pyarrow")
pq = pytest.importorskip("pyarrow.parquet")


def _write_quote_file(root: Path, instrument_id: str, date: str = "2023-01-03") -> Path:
    pdir = root / "cffex_l1_quote" / "data" / "quote_tick" / instrument_id
    pdir.mkdir(parents=True, exist_ok=True)
    path = pdir / f"{date}T01-29-00-200000000Z_{date}T06-59-59-200000000Z.parquet"
    table = pa.table(
        {
            "ts_event": pa.array([datetime(2023, 1, 3, 1, 29, tzinfo=timezone.utc)], pa.timestamp("ns", tz="UTC")),
            "bid_price": [100_000_000_000],
            "ask_price": [102_000_000_000],
        }
    )
    pq.write_table(table, path)
    return path


def test_dry_run_writes_nothing(tmp_path, capsys):
    root = tmp_path / "native"
    _write_quote_file(root, "IF2303.CFFEX")
    out = tmp_path / "outputs" / "derived_market_data" / "cffex_mid_bars"
    rc = main([
        "--native-catalog-root", str(root),
        "--out", str(out),
        "--symbols", "IF2303",
        "--dry-run",
    ])
    assert rc == 0
    assert "DRY_RUN_NO_WRITES" in capsys.readouterr().out
    assert not out.exists()


def test_plan_only_and_limits(tmp_path, capsys):
    root = tmp_path / "native"
    _write_quote_file(root, "IF2303.CFFEX", "2023-01-03")
    _write_quote_file(root, "IH2303.CFFEX", "2023-01-03")
    rc = main([
        "--native-catalog-root", str(root),
        "--out", str(tmp_path / "outputs" / "derived_market_data" / "cffex_mid_bars"),
        "--symbols", "IF2303,IH2303",
        "--plan-only",
        "--max-symbols", "1",
        "--max-days", "1",
    ])
    out = capsys.readouterr().out
    assert rc == 0
    assert "PLAN jobs=1" in out
    assert "IF2303.CFFEX" in out
    assert "IH2303.CFFEX" not in out


def test_output_guard_rejects_backtests_and_original_market_data(tmp_path):
    with pytest.raises(ValueError, match="output root"):
        safe_output_root(tmp_path / "elsewhere")
    with pytest.raises(ValueError, match="output root|backtests"):
        safe_output_root(tmp_path / "outputs" / "backtests" / "bad")
    with pytest.raises(ValueError, match="output root|original"):
        safe_output_root(tmp_path / "historical_data" / "market_data")


def test_existing_output_rejected(tmp_path):
    root = tmp_path / "native"
    _write_quote_file(root, "IF2303.CFFEX")
    out = tmp_path / "outputs" / "derived_market_data" / "cffex_mid_bars"
    out.mkdir(parents=True)
    with pytest.raises(FileExistsError):
        main([
            "--native-catalog-root", str(root),
            "--out", str(out),
            "--symbols", "IF2303",
        ])


def test_missing_native_catalog_and_symbol_raise(tmp_path):
    with pytest.raises(FileNotFoundError, match="native catalog root"):
        build_plan(native_catalog_root=tmp_path / "missing", symbols=["IF2303"], source="quote_tick")
    root = tmp_path / "native"
    root.mkdir()
    with pytest.raises(FileNotFoundError, match="quote_tick directory"):
        build_plan(native_catalog_root=root, symbols=["IF2303"], source="quote_tick")


def test_real_tmp_conversion_writes_derived_only(tmp_path):
    root = tmp_path / "native"
    _write_quote_file(root, "IF2303.CFFEX")
    out = tmp_path / "outputs" / "derived_market_data" / "cffex_mid_bars"
    rc = main([
        "--native-catalog-root", str(root),
        "--out", str(out),
        "--symbols", "IF2303",
        "--source", "quote_tick",
    ])
    assert rc == 0
    assert list(out.rglob("*.parquet"))
    assert not (tmp_path / "outputs" / "backtests").exists()


def test_cli_source_scan_has_no_network_backtest_or_destructive_ops():
    src = inspect.getsource(cli)
    forbidden = [
        "requests",
        "websocket",
        "Binance",
        "run_strategy",
        "BacktestEngine",
        "ScheduleWakeup",
        "shutil.rmtree",
        "shell=True",
        "outputs/backtests",
    ]
    for token in forbidden:
        assert token not in src
