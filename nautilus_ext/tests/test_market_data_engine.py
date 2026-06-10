"""Tests for the standalone market_data_engine data layer."""
from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from market_data_engine import BarEvent, load_events, make_bar_event, make_bars
from market_data_engine.adapters.bar_adapter import make_bar_event as adapter_make_bar_event
from market_data_engine.sources.csv_bars import CsvBarSource, load_csv_bars
from market_data_engine.sources.live_synthetic import LiveSyntheticBarSource
from market_data_engine.sources.synthetic import SyntheticBarSource
from market_data_engine.time import ONE_SECOND_NS, to_event_time_ns
from market_data_engine.validation import optional_numeric, require_numeric


def _write_csv(path: Path, header: str, rows: list[str]) -> Path:
    path.write_text(header + "\n" + "\n".join(rows) + "\n")
    return path


# A. BarEvent / adapter ------------------------------------------------------

class TestBarAdapter:

    def test_make_bar_event_defaults_from_close(self):
        bar = make_bar_event(close=100.0, instrument_id="BTC/USDT", event_time_ns=0)
        assert bar.open == bar.high == bar.low == 100.0
        assert bar.volume == 0.0
        assert bar.event_type == "bar"
        assert isinstance(bar, BarEvent)

    def test_make_bar_event_explicit_fields(self):
        bar = make_bar_event(close=10.0, open=9.0, high=11.0, low=8.0, volume=5.0,
                             instrument_id="X", event_time_ns=7)
        assert (bar.open, bar.high, bar.low, bar.volume) == (9.0, 11.0, 8.0, 5.0)

    def test_make_bars_monotonic_timestamps(self):
        bars = make_bars([100.0, 101.0, 102.0], instrument_id="ETH/USDT")
        assert [b.event_time_ns for b in bars] == [0, ONE_SECOND_NS, 2 * ONE_SECOND_NS]
        assert [b.close for b in bars] == [100.0, 101.0, 102.0]
        assert all(b.instrument_id == "ETH/USDT" for b in bars)
        assert adapter_make_bar_event is make_bar_event  # exported identity


# B. time conversion ---------------------------------------------------------

class TestTimeConversion:

    @pytest.mark.parametrize("unit,mult", [("ns", 1), ("us", 1_000), ("ms", 1_000_000), ("s", 1_000_000_000)])
    def test_units(self, unit, mult):
        assert to_event_time_ns(5, unit) == 5 * mult
        assert to_event_time_ns("5", unit) == 5 * mult  # numeric string

    def test_unsupported_unit_raises(self):
        with pytest.raises(ValueError, match="unsupported timestamp_unit"):
            to_event_time_ns(1, "minutes")

    def test_missing_value_raises(self):
        with pytest.raises(ValueError, match="required but missing"):
            to_event_time_ns("", "ns")


# C. validation --------------------------------------------------------------

class TestValidation:

    def test_require_numeric_accepts_values_and_strings(self):
        assert require_numeric(3, "x") == 3.0
        assert require_numeric("3.5", "x") == 3.5

    def test_require_numeric_malformed_names_field_and_row(self):
        with pytest.raises(ValueError, match=r"row 4: field 'close' is not numeric"):
            require_numeric("abc", "close", 4)

    def test_require_numeric_missing_raises(self):
        with pytest.raises(ValueError, match="required field 'close' is missing"):
            require_numeric("", "close")

    def test_optional_numeric_default_and_parse(self):
        assert optional_numeric("", 0.0, "volume") == 0.0
        assert optional_numeric("7", 0.0, "volume") == 7.0

    def test_optional_numeric_malformed_raises(self):
        with pytest.raises(ValueError, match="not numeric"):
            optional_numeric("xx", 0.0, "volume", 2)


# D. SyntheticBarSource ------------------------------------------------------

class TestSyntheticBarSource:

    def test_warmup_and_stream_counts(self):
        src = SyntheticBarSource(warmup_bars=20, live_bars=12)
        assert len(src.warmup()) == 20
        assert len(src.stream()) == 12

    def test_live_path_rise_and_fall(self):
        src = SyntheticBarSource(warmup_bars=20, live_bars=20)
        closes = [b.close for b in src.stream()]
        assert closes[0] == 100.0
        assert 110.0 in closes  # rise
        assert 80.0 in closes   # fall


# E. CsvBarSource ------------------------------------------------------------

class TestCsvBarSource:

    def test_full_fields(self, tmp_path):
        path = _write_csv(tmp_path / "b.csv", "event_time_ns,open,high,low,close,volume",
                          ["0,9,11,8,10,5"])
        src = CsvBarSource(path=str(path), instrument_id="X", warmup_bars=0)
        bar = src.stream()[0]
        assert (bar.open, bar.high, bar.low, bar.close, bar.volume) == (9.0, 11.0, 8.0, 10.0, 5.0)

    def test_missing_optional_fields_default(self, tmp_path):
        path = _write_csv(tmp_path / "c.csv", "close", ["100"])
        src = CsvBarSource(path=str(path), instrument_id="X", warmup_bars=0)
        bar = src.stream()[0]
        assert bar.open == bar.high == bar.low == 100.0 and bar.volume == 0.0

    def test_generated_timestamps(self, tmp_path):
        path = _write_csv(tmp_path / "d.csv", "close", ["100", "101"])
        src = CsvBarSource(path=str(path), instrument_id="X", warmup_bars=0)
        assert [b.event_time_ns for b in src.stream()] == [0, ONE_SECOND_NS]

    def test_unit_conversion(self, tmp_path):
        path = _write_csv(tmp_path / "e.csv", "ts,close", ["5,100"])
        src = CsvBarSource(path=str(path), instrument_id="X", warmup_bars=0,
                           timestamp_column="ts", timestamp_unit="ms")
        assert src.stream()[0].event_time_ns == 5 * 1_000_000

    def test_sorted_and_split(self, tmp_path):
        path = _write_csv(tmp_path / "f.csv", "ts,close", ["3,103", "1,101", "2,102"])
        src = CsvBarSource(path=str(path), instrument_id="X", warmup_bars=1,
                           timestamp_column="ts", timestamp_unit="ns")
        warmup, live = src.warmup(), src.stream()
        assert [b.event_time_ns for b in warmup] == [1]
        assert [b.event_time_ns for b in live] == [2, 3]

    def test_malformed_close_raises(self, tmp_path):
        path = _write_csv(tmp_path / "g.csv", "close", ["100", "abc"])
        with pytest.raises(ValueError, match="not numeric"):
            CsvBarSource(path=str(path), instrument_id="X").stream()

    def test_unsupported_unit_raises_in_init(self):
        with pytest.raises(ValueError, match="unsupported timestamp_unit"):
            CsvBarSource(path="x", instrument_id="X", timestamp_unit="weeks")

    def test_malformed_timestamp_error_includes_row(self, tmp_path):
        path = _write_csv(tmp_path / "ts.csv", "ts,close", ["0,100", "oops,101"])
        src = CsvBarSource(path=str(path), instrument_id="X", warmup_bars=0,
                           timestamp_column="ts", timestamp_unit="ns")
        with pytest.raises(ValueError, match=r"row 1: "):
            src.stream()

    def test_file_read_once_across_warmup_and_stream(self, tmp_path, monkeypatch):
        path = _write_csv(tmp_path / "once.csv", "close", ["100", "101", "102", "103"])
        src = CsvBarSource(path=str(path), instrument_id="X", warmup_bars=2)

        calls = {"n": 0}
        original = src._load_sorted

        def counted():
            calls["n"] += 1
            return original()

        monkeypatch.setattr(src, "_load_sorted", counted)
        warmup, live = src.warmup(), src.stream()
        assert [b.close for b in warmup] == [100.0, 101.0]
        assert [b.close for b in live] == [102.0, 103.0]
        assert calls["n"] == 1  # cached: the file is read exactly once


# F. LiveSyntheticBarSource --------------------------------------------------

class TestLiveSyntheticBarSource:

    def test_warmup_list_stream_generator(self):
        src = LiveSyntheticBarSource(warmup_bars=5, live_bars=5)
        assert isinstance(src.warmup(), list)
        assert isinstance(src.stream(), Iterator)

    def test_delay_zero_drains(self):
        src = LiveSyntheticBarSource(warmup_bars=3, live_bars=4, delay_seconds=0.0)
        assert len(list(src.stream())) == 4


# G. loader ------------------------------------------------------------------

class TestLoader:

    def test_synthetic(self):
        warmup, live = load_events({"mode": "synthetic", "warmup_bars": 6, "live_bars": 4})
        assert len(warmup) == 6 and len(list(live)) == 4

    def test_csv_bars(self, tmp_path):
        path = _write_csv(tmp_path / "h.csv", "close", ["100"] * 8)
        warmup, live = load_events({"mode": "csv_bars", "path": str(path), "warmup_bars": 5})
        assert len(warmup) == 5 and len(list(live)) == 3

    def test_live_synthetic(self):
        warmup, live = load_events({"mode": "live_synthetic", "warmup_bars": 5, "live_bars": 5})
        assert len(warmup) == 5 and isinstance(live, Iterator)

    def test_default_mode_is_synthetic(self):
        warmup, live = load_events({})
        assert warmup and list(live)

    def test_unsupported_mode_lists_supported(self):
        with pytest.raises(ValueError) as exc:
            load_events({"mode": "kafka"})
        msg = str(exc.value)
        assert "kafka" in msg
        for mode in ("synthetic", "csv_bars", "live_synthetic"):
            assert mode in msg


# H. compatibility -----------------------------------------------------------

class TestCompatibility:

    def test_strategy_framework_data_loaders_delegates(self):
        import strategy_framework.data_loaders as dl
        from market_data_engine.loader import load_events as canonical

        assert dl.load_events is canonical
        warmup, live = dl.load_events({"mode": "synthetic", "warmup_bars": 5, "live_bars": 5})
        assert len(warmup) == 5

    def test_synthetic_bars_reexport(self):
        from nautilus_ext.features.examples.synthetic_bars import (
            ONE_SECOND_NS as compat_ns,
            BarEvent as CompatBar,
            make_bars as compat_make_bars,
        )

        assert compat_ns == ONE_SECOND_NS
        assert CompatBar is BarEvent
        assert compat_make_bars is make_bars
        assert len(compat_make_bars([1.0, 2.0])) == 2

    def test_data_loaders_has_no_logic(self):
        import inspect

        import strategy_framework.data_loaders as dl

        src = inspect.getsource(dl)
        # Pure re-export wrapper: no stdlib csv parsing, no inline price path,
        # and no local implementation of load_events.
        assert "import csv" not in src
        assert "110.0" not in src
        assert "def load_events" not in src

    def test_live_synthetic_imports_public_helper_only(self):
        import inspect

        import market_data_engine.sources.live_synthetic as ls

        src = inspect.getsource(ls)
        # Must use the public demo_closes, never the old private name.
        assert "_demo_closes" not in src
        assert "demo_closes" in src


# I. run_strategy CSV path resolution from any CWD ---------------------------

class TestRunStrategyCsvPathResolution:

    def test_csv_config_runs_from_non_root_cwd(self, tmp_path, monkeypatch, capsys):
        import run_strategy

        repo_root = Path(run_strategy.__file__).resolve().parent
        config = repo_root / "strategies" / "ma_crossover" / "config_backtest.yaml"

        # Run from an unrelated CWD: the relative data.path in the config must
        # still resolve against the repo root, not the caller's directory.
        monkeypatch.chdir(tmp_path)
        run_strategy.main(["--config", str(config)])
        assert "[ma_crossover] warmed up" in capsys.readouterr().out
