"""HistoricalFeatureBuilder feature_data layout tests."""
from __future__ import annotations

import polars as pl

from feature_engine.features import load_all
from feature_engine.services import HistoricalFeatureBuilder
from feature_engine.storage import FeatureDataReader


def test_historical_feature_builder_writes_new_layout_by_default(tmp_path) -> None:
    load_all()
    feature_root = tmp_path / "feature_data"
    df = pl.DataFrame(
        {
            "symbol": ["IH2303.CFFEX", "IH2303.CFFEX"],
            "ts_event": [1, 2],
            "sma_20": [None, 10.0],
            "vwm_20": [None, 0.01],
        }
    )
    paths = HistoricalFeatureBuilder(["sma_20", "vwm_20"]).write_feature_data(
        df,
        feature_root=feature_root,
        asset_class="future",
        exchange="CFFEX",
        frequency="1m",
        trading_date="2026-05-26",
        instrument_id="IH2303.CFFEX",
    )

    assert paths
    joined = "\n".join(p.as_posix() for p in paths)
    assert "feature_group=technical/asset_class=future/exchange=CFFEX" in joined
    assert "frequency=1m/trading_date=2026-05-26/instrument_id=IH2303.CFFEX" in joined
    assert "feature_group=volume/asset_class=future/exchange=CFFEX" in joined

    got = FeatureDataReader(feature_root).scan_features(
        frequency="1m",
        trading_date="2026-05-26",
        instrument_id="IH2303.CFFEX",
    )
    assert got.height == 2
    assert "sma_20" in got.columns
    assert "vwm_20" in got.columns


def test_historical_feature_builder_can_write_legacy_layout_explicitly(tmp_path) -> None:
    load_all()
    feature_root = tmp_path / "feature_data"
    df = pl.DataFrame(
        {"symbol": ["IH2303.CFFEX"], "ts_event": [1], "sma_20": [10.0]}
    )
    paths = HistoricalFeatureBuilder(["sma_20"]).write_feature_data(
        df,
        feature_root=feature_root,
        asset_class="future",
        exchange="CFFEX",
        frequency="1m",
        trading_date="2026-05-26",
        instrument_id="IH2303.CFFEX",
        legacy_layout=True,
    )

    assert paths
    path = paths[0].as_posix()
    assert "feature_group=technical/frequency=1m/trading_date=2026-05-26" in path
    assert "instrument_id=IH2303.CFFEX" not in path
    got = FeatureDataReader(feature_root).scan_features(
        frequency="1m",
        trading_date="2026-05-26",
        instrument_id="IH2303.CFFEX",
    )
    assert got.height == 1
