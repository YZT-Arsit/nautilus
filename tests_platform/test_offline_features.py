"""Offline feature builder: same operators as live, written as feature_data."""
import pytest

pytest.importorskip("polars", reason="offline builder needs polars (server env)")

from data_engine.adapters.bar_adapter import make_bars  # noqa: E402
from feature_engine.api import rolling_mean_spec  # noqa: E402
from feature_engine.offline import HistoricalFeatureBuilder  # noqa: E402


def test_build_from_events_produces_feature_columns():
    bars = make_bars([100 + i * 0.5 for i in range(50)], instrument_id="BTCUSDT")
    specs = [rolling_mean_spec("ma5", window=5), rolling_mean_spec("ma10", window=10)]
    df = HistoricalFeatureBuilder(specs).build_from_events(bars)
    assert df.height == 50
    assert {"symbol", "ts_event", "ma5", "ma10"} <= set(df.columns)
    # ma5 ready by the 5th bar (point-in-time: earlier rows are null).
    assert df["ma5"][:4].null_count() == 4
    assert df["ma5"][4] is not None


def test_write_feature_data_roundtrip(tmp_path):
    bars = make_bars([100 + i for i in range(30)], instrument_id="BTCUSDT")
    builder = HistoricalFeatureBuilder([rolling_mean_spec("ma5", window=5)],
                                       feature_group="technical")
    df = builder.build_from_events(bars)
    written = builder.write_feature_data(
        df, feature_root=str(tmp_path / "feature_data"),
        asset_class="crypto", exchange="BINANCE", frequency="1m",
        trading_date="2026-05-26", instrument_id="BTCUSDT",
    )
    assert written and all(p.suffix == ".parquet" for p in written)
