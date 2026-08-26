from __future__ import annotations

from pathlib import Path

import pandas as pd

from scripts.internal.build_episode_diagnostics import build_corrected_canonical_summary
from scripts.internal.build_episode_diagnostics import discover
from scripts.internal.build_episode_diagnostics import metadata_from_path


def test_boss_discovery_excludes_secondary_direction_filters(tmp_path) -> None:
    for mode in ("original", "long_only", "short_only", "strict_reverse"):
        path = tmp_path / f"strategy/BTCUSDT/1m/lag0m/{mode}/per_trade_break_even.csv"
        path.parent.mkdir(parents=True)
        path.write_text("header\n", encoding="utf-8")
    paths = discover(tmp_path, None)
    assert {path.parent.name for path in paths} == {"original", "strict_reverse"}


def test_original_source_mode_is_presented_as_normal(tmp_path) -> None:
    source = tmp_path / "strategy/BTCUSDT/1m/lag1m/original/per_trade_break_even.csv"
    metadata = metadata_from_path(source, tmp_path)
    assert metadata["source_variant"] == "original"
    assert metadata["direction_mode"] == "normal"


def test_corrected_summary_has_exactly_two_boss_modes(tmp_path) -> None:
    source = tmp_path / "source"
    output = tmp_path / "output"
    source.mkdir()
    rows = []
    for mode in ("original", "long_only", "short_only", "strict_reverse"):
        for premium in ("included", "excluded"):
            rows.append(
                {
                    "strategy": "strategy",
                    "symbol": "BTCUSDT",
                    "timeframe": "1m",
                    "lag_minutes": 0,
                    "lag": "0m additional execution lag",
                    "variant": mode,
                    "premium": premium,
                    "figure_relative": "old.png",
                }
            )
    pd.DataFrame(rows).to_csv(source / "canonical_summary.csv", index=False)
    corrected = build_corrected_canonical_summary(source, output)
    assert set(corrected["variant"]) == {"normal", "strict_reverse"}
    assert len(corrected) == 4
    assert not corrected["figure_relative"].str.contains("long_only|short_only|original").any()
