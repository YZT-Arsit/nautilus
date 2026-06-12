"""端到端 CLI: scripts/build_historical_features.py。

本地可跑，无网络、无 pandas。
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

pl = pytest.importorskip("polars")

REPO_ROOT = Path(__file__).resolve().parents[2]
_SCRIPT = REPO_ROOT / "scripts" / "build_historical_features.py"


def _load_cli():
    spec = importlib.util.spec_from_file_location("build_historical_features", _SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="session", autouse=True)
def _register_features() -> None:
    from feature_engine.features import load_all

    load_all()


def _write_csv(path: Path, n: int = 60) -> None:
    rows = ["event_time_ns,open,high,low,close,volume"]
    price = 100.0
    for i in range(n):
        price += 0.5 if i % 2 == 0 else -0.3
        rows.append(
            f"{i * 1_000_000_000},{price - 0.2},{price + 0.3},{price - 0.4},{price},{1000 + i}"
        )
    path.write_text("\n".join(rows) + "\n")


def test_cli_end_to_end_from_csv(tmp_path) -> None:
    cli = _load_cli()
    csv = tmp_path / "bars.csv"
    _write_csv(csv, n=60)

    feature_root = tmp_path / "feature_data"
    manifest_root = tmp_path / "manifests"

    report = cli.main(
        [
            "--csv",
            str(csv),
            "--feature-root",
            str(feature_root),
            "--manifest-root",
            str(manifest_root),
            "--trading-date",
            "2026-05-26",
            "--instrument-id",
            "IH2303.CFFEX",
            "--frequency",
            "1m",
            "--features",
            "sma_20,rsi_14,vwm_20",
            "--mode",
            "overwrite",
        ]
    )

    assert report["partitions_written"] > 0
    assert report["manifest_rows"] >= 3
    assert report["run_id"]

    # 用 FeatureDataReader 把刚写的特征数据读回来验证闭环。
    from feature_engine.storage import FeatureDataReader

    reader = FeatureDataReader(feature_root, manifest_root=manifest_root)
    df = reader.scan_features(trading_date="2026-05-26", frequency="1m")
    # 跨 feature_group（technical: sma/rsi, volume: vwm）合并回一行/时间戳。
    assert df.height == 60
    assert "sma_20" in df.columns
    assert "vwm_20" in df.columns
    # 暖机后应有非空 sma_20。
    assert df["sma_20"].drop_nulls().len() > 0

    avail = reader.available_features(trading_date="2026-05-26")
    names = set(avail["feature_name"].to_list())
    assert {"sma_20", "rsi_14", "vwm_20"} <= names


def test_cli_unknown_feature_errors(tmp_path) -> None:
    cli = _load_cli()
    csv = tmp_path / "bars.csv"
    _write_csv(csv, n=30)
    with pytest.raises(SystemExit):
        cli.main(
            [
                "--csv",
                str(csv),
                "--feature-root",
                str(tmp_path / "f"),
                "--manifest-root",
                str(tmp_path / "m"),
                "--trading-date",
                "2026-05-26",
                "--instrument-id",
                "X.Y",
                "--features",
                "not_a_real_feature",
            ]
        )


def test_cli_no_source_errors(tmp_path) -> None:
    cli = _load_cli()
    with pytest.raises(SystemExit):
        cli.main(
            [
                "--feature-root",
                str(tmp_path / "f"),
                "--manifest-root",
                str(tmp_path / "m"),
                "--trading-date",
                "2026-05-26",
                "--instrument-id",
                "X.Y",
                "--features",
                "sma_20",
            ]
        )
