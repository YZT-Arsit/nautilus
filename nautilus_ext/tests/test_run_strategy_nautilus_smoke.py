"""End-to-end smoke tests for ``run_strategy.py`` with the Nautilus backtest backend.

Two layers:

* **simulated** smoke - full ``run_strategy`` chain (data_engine -> feature_engine
  -> strategy -> signal -> intent -> backend -> report) with the dependency-free
  fill model. Runs anywhere ``yaml`` is available; no Nautilus needed.
* **native** smoke - the SAME chain but ``mode=nautilus_native``, which builds and
  runs a real Nautilus ``BacktestEngine``. Guarded by ``importorskip`` so it is
  skipped where ``nautilus_trader`` is not built (e.g. the dev laptop) and runs on
  the backtest server. It asserts the native path no longer raises the placeholder
  ``NotImplementedError`` and emits ``metrics.json`` + ``report.md``.

No network access; data is synthetic.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")

import run_strategy  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[2]
NATIVE_CONFIG = REPO_ROOT / "configs" / "backtests" / "ma_crossover_nautilus_synthetic.yaml"


def _write_config(tmp_path: Path, *, mode: str, run_name: str) -> Path:
    cfg = yaml.safe_load(NATIVE_CONFIG.read_text())
    cfg["run_name"] = run_name
    cfg["execution"]["mode"] = mode
    cfg["output"] = {"root": str(tmp_path), "print_table": False}
    path = tmp_path / f"{run_name}.yaml"
    path.write_text(yaml.safe_dump(cfg, sort_keys=False))
    return path


def _assert_report(out_dir: Path, expected_mode: str) -> dict:
    metrics_path = out_dir / "metrics.json"
    report_path = out_dir / "report.md"
    assert metrics_path.exists(), f"missing metrics.json in {out_dir}"
    assert report_path.exists(), f"missing report.md in {out_dir}"
    metrics = json.loads(metrics_path.read_text())
    assert metrics["mode"] == expected_mode
    assert metrics["bar_count"] > 0
    for key in ("total_return", "max_drawdown", "trade_count", "final_equity",
                "initial_cash", "start_time", "end_time", "signal_count"):
        assert key in metrics, key
    return metrics


def test_run_strategy_config_is_valid_yaml():
    cfg = yaml.safe_load(NATIVE_CONFIG.read_text())
    assert cfg["strategy"] == "ma_crossover"
    assert cfg["execution"]["backend"] == "nautilus_backtest"
    assert cfg["execution"]["mode"] == "nautilus_native"


def test_simulated_smoke_writes_metrics_and_report(tmp_path: Path):
    config_path = _write_config(tmp_path, mode="simulated", run_name="sim_smoke")
    run_strategy.main(["--config", str(config_path)])
    metrics = _assert_report(tmp_path / "sim_smoke", "simulated")
    # synthetic MA path produces one BUY then one SELL -> one closed trade
    assert metrics["trade_count"] >= 1


def test_native_smoke_runs_real_engine(tmp_path: Path):
    pytest.importorskip("nautilus_trader")
    pytest.importorskip("pandas")
    config_path = _write_config(tmp_path, mode="nautilus_native", run_name="native_smoke")
    # Must complete without the placeholder NotImplementedError.
    run_strategy.main(["--config", str(config_path)])
    metrics = _assert_report(tmp_path / "native_smoke", "nautilus_native")
    # native fills come from the Nautilus matching engine
    assert metrics["fill_count"] >= 1
    assert "engine" in metrics and metrics["engine"].get("engine") == "BacktestEngine"
