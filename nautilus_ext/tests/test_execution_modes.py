"""Tests for the three execution modes: synthetic, csv_bars, live_synthetic.

Also covers the signal recorder and the layering boundaries introduced for
backtest/live support.
"""
from __future__ import annotations

import inspect
from collections.abc import Iterator
from pathlib import Path

import pytest

import run_strategy
from strategy_framework import data_loaders, output
from strategy_framework.backtest import SignalRecord, SignalRecorder
from strategy_framework.data_loaders import (
    load_csv_bars,
    load_events,
    load_live_synthetic,
)
from strategies.ma_crossover import (
    MovingAverageCrossoverConfig,
    MovingAverageCrossoverStrategy,
    build_specs,
)
from nautilus_ext.features.examples.synthetic_bars import ONE_SECOND_NS, BarEvent
from nautilus_ext.features.runner import FeatureStrategyRunner

REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIGS = REPO_ROOT / "strategies" / "ma_crossover"


def _write_csv(path: Path, header: str, rows: list[str]) -> Path:
    path.write_text(header + "\n" + "\n".join(rows) + "\n")
    return path


# ===========================================================================
# A. csv_bars loader
# ===========================================================================

class TestCsvBarsLoader:

    def _full_csv(self, tmp_path: Path) -> Path:
        header = "event_time_ns,open,high,low,close,volume"
        rows = [f"{i * ONE_SECOND_NS},{100 + i},{100 + i + 1},{100 + i - 1},{100 + i},{10 + i}"
                for i in range(25)]
        return _write_csv(tmp_path / "bars.csv", header, rows)

    def test_warmup_live_split(self, tmp_path: Path):
        cfg = {"mode": "csv_bars", "path": str(self._full_csv(tmp_path)), "warmup_bars": 20}
        warmup, live = load_csv_bars(cfg)
        assert len(warmup) == 20
        assert len(live) == 5

    def test_ohlcv_parsed(self, tmp_path: Path):
        cfg = {
            "mode": "csv_bars", "path": str(self._full_csv(tmp_path)), "warmup_bars": 0,
            "open_column": "open", "high_column": "high", "low_column": "low",
            "close_column": "close", "volume_column": "volume",
        }
        _, live = load_csv_bars(cfg)
        first = live[0]
        assert isinstance(first, BarEvent)
        assert first.close == 100.0
        assert first.open == 100.0
        assert first.high == 101.0
        assert first.low == 99.0
        assert first.volume == 10.0

    def test_defaults_when_ohlv_columns_missing(self, tmp_path: Path):
        path = _write_csv(tmp_path / "close_only.csv", "close", ["100", "101"])
        cfg = {"mode": "csv_bars", "path": str(path), "warmup_bars": 0}
        _, live = load_csv_bars(cfg)
        bar = live[0]
        assert bar.open == bar.high == bar.low == bar.close == 100.0
        assert bar.volume == 0.0

    def test_generated_timestamps_when_column_absent(self, tmp_path: Path):
        path = _write_csv(tmp_path / "no_ts.csv", "close", ["100", "101", "102"])
        cfg = {"mode": "csv_bars", "path": str(path), "warmup_bars": 0}
        _, live = load_csv_bars(cfg)
        assert [b.event_time_ns for b in live] == [0, ONE_SECOND_NS, 2 * ONE_SECOND_NS]

    @pytest.mark.parametrize("unit,multiplier", [("ns", 1), ("us", 1_000), ("ms", 1_000_000), ("s", 1_000_000_000)])
    def test_timestamp_unit_conversion(self, tmp_path: Path, unit: str, multiplier: int):
        path = _write_csv(tmp_path / f"ts_{unit}.csv", "ts,close", ["5,100", "6,101"])
        cfg = {"mode": "csv_bars", "path": str(path), "warmup_bars": 0,
               "timestamp_column": "ts", "timestamp_unit": unit}
        _, live = load_csv_bars(cfg)
        assert live[0].event_time_ns == 5 * multiplier

    def test_sorted_by_event_time(self, tmp_path: Path):
        path = _write_csv(tmp_path / "unsorted.csv", "ts,close", ["3,103", "1,101", "2,102"])
        cfg = {"mode": "csv_bars", "path": str(path), "warmup_bars": 0,
               "timestamp_column": "ts", "timestamp_unit": "ns"}
        _, live = load_csv_bars(cfg)
        assert [b.event_time_ns for b in live] == [1, 2, 3]

    def test_malformed_close_raises(self, tmp_path: Path):
        path = _write_csv(tmp_path / "bad.csv", "close", ["100", "abc"])
        cfg = {"mode": "csv_bars", "path": str(path), "warmup_bars": 0}
        with pytest.raises(ValueError, match="not numeric"):
            load_csv_bars(cfg)

    def test_missing_close_raises(self, tmp_path: Path):
        path = _write_csv(tmp_path / "noclose.csv", "open", ["100"])
        cfg = {"mode": "csv_bars", "path": str(path), "warmup_bars": 0}
        with pytest.raises(ValueError, match="close column"):
            load_csv_bars(cfg)

    def test_unsupported_timestamp_unit_raises(self, tmp_path: Path):
        path = _write_csv(tmp_path / "u.csv", "ts,close", ["1,100"])
        cfg = {"mode": "csv_bars", "path": str(path), "warmup_bars": 0,
               "timestamp_column": "ts", "timestamp_unit": "minutes"}
        with pytest.raises(ValueError, match="unsupported timestamp_unit"):
            load_csv_bars(cfg)

    def test_missing_path_raises(self):
        with pytest.raises(ValueError, match="requires a 'path'"):
            load_csv_bars({"mode": "csv_bars"})


# ===========================================================================
# B. live_synthetic loader
# ===========================================================================

class TestLiveSyntheticLoader:

    def test_returns_list_warmup_and_iterable_live(self):
        warmup, live = load_live_synthetic({"warmup_bars": 20, "live_bars": 10})
        assert isinstance(warmup, list)
        assert len(warmup) == 20
        assert isinstance(live, Iterator)  # a generator, not a list

    def test_live_events_consumable_by_runner(self):
        config = MovingAverageCrossoverConfig()
        runner = FeatureStrategyRunner(build_specs(config), MovingAverageCrossoverStrategy(config))
        warmup, live = load_live_synthetic({"warmup_bars": 20, "live_bars": 20})
        runner.warmup(iter(warmup))
        signals = [signal for _, _, signal in runner.run(live)]
        assert "BUY" in signals
        assert "SELL" in signals

    def test_delay_zero_does_not_block(self):
        # delay_seconds=0.0 must not sleep; draining is effectively instant.
        _, live = load_live_synthetic({"warmup_bars": 5, "live_bars": 5, "delay_seconds": 0.0})
        assert len(list(live)) == 5

    def test_via_load_events_dispatch(self):
        warmup, live = load_events({"mode": "live_synthetic", "warmup_bars": 5, "live_bars": 5})
        assert len(warmup) == 5
        assert isinstance(live, Iterator)


# ===========================================================================
# C. backtest recorder
# ===========================================================================

class _StubSnapshot:
    def __init__(self, values: dict[str, float | None]) -> None:
        self._values = values

    def value(self, name: str, default=None):
        return self._values.get(name, default)


class TestSignalRecorder:

    def test_records_all_fields(self):
        rec = SignalRecorder(["ma5_close", "ma20_close"])
        bar = BarEvent(close=110.0, open=110.0, high=111.0, low=109.0, volume=1.0,
                       instrument_id="BTC/USDT", event_time_ns=21 * ONE_SECOND_NS)
        snap = _StubSnapshot({"ma5_close": 102.0, "ma20_close": 100.5})
        rec.record(bar, snap, "BUY")

        (record,) = rec.records()
        assert isinstance(record, SignalRecord)
        assert record.event_time_ns == 21 * ONE_SECOND_NS
        assert record.instrument_id == "BTC/USDT"
        assert record.signal == "BUY"
        assert record.close == 110.0
        assert record.values == {"ma5_close": 102.0, "ma20_close": 100.5}

    def test_signal_counts(self):
        rec = SignalRecorder(["ma5_close"])
        snap = _StubSnapshot({"ma5_close": 1.0})
        bar = BarEvent(close=1.0, open=1.0, high=1.0, low=1.0, volume=0.0,
                       instrument_id="X", event_time_ns=0)
        for sig in ["HOLD", "BUY", "HOLD", "SELL", "HOLD"]:
            rec.record(bar, snap, sig)
        assert rec.signal_counts() == {"HOLD": 3, "BUY": 1, "SELL": 1}

    def test_to_rows_are_plain_dicts(self):
        rec = SignalRecorder(["ma5_close"])
        snap = _StubSnapshot({"ma5_close": 100.0})
        bar = BarEvent(close=100.0, open=100.0, high=100.0, low=100.0, volume=0.0,
                       instrument_id="X", event_time_ns=42)
        rec.record(bar, snap, "HOLD")
        rows = rec.to_rows()
        assert rows == [{"event_time_ns": 42, "instrument_id": "X", "signal": "HOLD",
                         "close": 100.0, "ma5_close": 100.0}]
        assert all(isinstance(r, dict) for r in rows)

    def test_recorder_defensive_on_bare_event(self):
        rec = SignalRecorder(["ma5_close"])
        rec.record(object(), _StubSnapshot({"ma5_close": None}), "HOLD")
        (record,) = rec.records()
        assert record.event_time_ns is None
        assert record.close is None
        assert record.instrument_id is None


# ===========================================================================
# D. run_strategy integration across modes
# ===========================================================================

class TestRunStrategyModes:

    def test_synthetic_config(self, capsys):
        run_strategy.main(["--config", str(CONFIGS / "config.yaml")])
        out = capsys.readouterr().out
        assert "BUY" in out and "SELL" in out

    def test_csv_backtest_config(self, capsys):
        run_strategy.main(["--config", str(CONFIGS / "config_backtest.yaml")])
        out = capsys.readouterr().out
        assert "BUY" in out and "SELL" in out
        assert "signal counts:" in out  # record_signals: true

    def test_live_synthetic_config(self, capsys):
        run_strategy.main(["--config", str(CONFIGS / "config_live_synthetic.yaml")])
        out = capsys.readouterr().out
        assert "BUY" in out and "SELL" in out
        assert "signal counts:" in out

    def test_record_signals_summary_counts(self, capsys):
        run_strategy.main(["--config", str(CONFIGS / "config_backtest.yaml")])
        out = capsys.readouterr().out
        assert "BUY=1" in out and "SELL=1" in out

    def test_legacy_wrapper_still_works(self, capsys):
        import scripts.run_ma_crossover_demo as legacy

        legacy.main(["--config", str(CONFIGS / "config.yaml")])
        assert "[ma_crossover] warmed up" in capsys.readouterr().out


# ===========================================================================
# E. boundary tests
# ===========================================================================

class TestExecutionBoundaries:

    FORBIDDEN = (
        "nautilus_ext.features.compute.features",
        "nautilus_ext.features.compute.backend",
        "nautilus_ext.features.compute.state",
        "nautilus_ext.features.compute.engine",
    )

    def test_new_modules_have_no_compute_internal_imports(self):
        from strategy_framework import backtest, live_sources

        for module in (data_loaders, output, run_strategy, backtest, live_sources):
            src = inspect.getsource(module)
            for forbidden in self.FORBIDDEN:
                assert forbidden not in src, f"{module.__name__} must not import {forbidden}"

    def test_run_strategy_has_no_inline_data_or_format_logic(self):
        src = inspect.getsource(run_strategy)
        assert "make_bars" not in src
        assert "live_closes" not in src
        assert "110.0" not in src
        assert "csv" not in src  # csv parsing lives in data_loaders

    def test_run_strategy_delegates_to_data_loaders_and_output(self):
        src = inspect.getsource(run_strategy)
        assert "from strategy_framework.data_loaders import load_events" in src
        assert "load_events(" in src
        assert "from strategy_framework import output" in src
        assert "output." in src

    def test_compute_features_not_imported_anywhere_in_user_layer(self):
        for module in (data_loaders, output, run_strategy):
            assert "compute.features" not in inspect.getsource(module)
