"""Fee-scenario tests: with/without-fee paired runs produce distinct reports."""
import json
from pathlib import Path

from run_strategy import run_config


def _cfg(root: str) -> dict:
    return {
        "strategy": "ma_crossover",
        "params": {"fast_window": 5, "slow_window": 20},
        "data": {"mode": "synthetic", "warmup_bars": 20, "live_bars": 80},
        "output": {"print_table": False, "root": root},
        "execution": {
            "backend": "nautilus_backtest",
            "mode": "simulated",
            "initial_cash": 100_000,
            "quantity": 1.0,
            "fee_scenarios": [0.0, 0.0005],
        },
    }


def test_fee_scenarios_paired(tmp_path):
    results = run_config(_cfg(str(tmp_path)))
    assert len(results) == 2
    assert {r["fee"] for r in results} == {0.0, 0.0005}
    # Each scenario wrote its own metrics.json in a distinct directory.
    dirs = {r["output_dir"] for r in results}
    assert len(dirs) == 2
    for r in results:
        assert (Path(r["output_dir"]) / "metrics.json").is_file()
        json.loads((Path(r["output_dir"]) / "metrics.json").read_text(encoding="utf-8"))
