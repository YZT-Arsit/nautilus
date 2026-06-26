from __future__ import annotations

import csv
import inspect
import json
import subprocess
from types import SimpleNamespace
from pathlib import Path

import pytest
import yaml

import scripts.run_vwm_batch_backtests as runner


def _config(tmp_path: Path) -> dict:
    return {
        "strategy": {
            "name": "vwm",
            "params": {"mom_len": 5, "avg_len": 20, "atr_len": 5, "atr_pct": 0.5, "setup_len": 5},
        },
        "execution": {
            "backend": "nautilus",
            "fill_timing": "same_bar",
            "fee_rate": 0.0005,
            "initial_cash": 100000,
        },
        "data": {
            "root": str(tmp_path / "historical_data" / "market_data"),
            "bar_type": "1m",
            "start": "2026-06-10",
            "end": "2026-06-12",
        },
        "universe": {
            "include": [
                {"exchange": "BINANCE", "venue_type": "spot", "symbol": "BTCUSDT", "bar_type": "1m"},
                {"exchange": "BINANCE", "venue_type": "spot", "symbol": "ETHUSDT", "bar_type": "1m"},
            ],
            "exclude": [],
        },
        "output": {"root": str(tmp_path / "outputs" / "backtests" / "vwm_batch"), "overwrite": False},
    }


def _smoke_config(tmp_path: Path, *, symbols: list[str] | None = None) -> dict:
    cfg = _config(tmp_path)
    symbols = symbols or ["BTCUSDT", "ETHUSDT"]
    cfg["data"]["start"] = "2026-06-10"
    cfg["data"]["end"] = "2026-06-12"
    cfg["output"]["root"] = str(tmp_path / "outputs" / "backtests" / "vwm_batch_smoke")
    cfg["universe"]["include"] = [
        {"exchange": "BINANCE", "venue_type": "spot", "symbol": symbol, "bar_type": "1m"}
        for symbol in symbols
    ]
    return cfg


def test_config_parse_validate_and_rejects_bad_strategy(tmp_path):
    cfg = _config(tmp_path)
    path = tmp_path / "cfg.yaml"
    path.write_text(yaml.safe_dump(cfg), encoding="utf-8")
    loaded = runner.load_batch_config(path)
    runner.validate_batch_config(loaded)
    loaded["strategy"]["name"] = "not_vwm"
    with pytest.raises(ValueError, match="strategy.name"):
        runner.validate_batch_config(loaded)


def test_config_rejects_invalid_dates_and_output_guard(tmp_path):
    cfg = _config(tmp_path)
    cfg["data"]["end"] = "2026-06-09"
    with pytest.raises(ValueError, match="before start"):
        runner.validate_batch_config(cfg)
    cfg = _config(tmp_path)
    cfg["output"]["root"] = str(tmp_path / "tmp_runs")
    with pytest.raises(ValueError, match="outputs/backtests"):
        runner.validate_batch_config(cfg)


def test_e4_multisymbol_smoke_output_prefix_allowed(tmp_path):
    out = tmp_path / "outputs" / "backtests" / "crypto_perpetual_multisymbol_vwm_smoke"
    runner._ensure_smoke_output_root(out)


def test_dry_run_builds_jobs_and_max_symbols(tmp_path):
    cfg = _config(tmp_path)
    jobs = runner.build_jobs(cfg, max_symbols=1, start="2026-06-11", end="2026-06-12")
    assert len(jobs) == 1
    assert jobs[0].symbol == "BTCUSDT"
    assert jobs[0].start == "2026-06-11"
    assert jobs[0].end == "2026-06-12"
    assert Path(jobs[0].output_dir).parts[-3:-1] == ("backtests", "vwm_batch")
    assert jobs[0].params_hash


def test_build_jobs_preserves_explicit_instrument_id(tmp_path):
    cfg = _config(tmp_path)
    cfg["data"]["bar_type"] = "5m"
    cfg["output"]["root"] = str(tmp_path / "outputs" / "backtests" / "crypto_perpetual_vwm_smoke")
    cfg["universe"]["include"] = [
        {
            "exchange": "BINANCE",
            "venue_type": "futures_um",
            "symbol": "BTCUSDT",
            "instrument_id": "BTCUSDT-PERP.BINANCE",
            "bar_type": "5m",
        }
    ]
    [job] = runner.build_jobs(cfg)
    assert job.instrument_id == "BTCUSDT-PERP.BINANCE"


def _write_success_run(path: Path, *, total_return: float, max_drawdown: float, pnl: float) -> None:
    path.mkdir(parents=True)
    (path / "metrics.json").write_text(
        json.dumps(
            {
                "initial_cash": 100000,
                "final_equity": 100000 * (1 + total_return),
                "total_return": total_return,
                "gross_realized_pnl": pnl,
                "net_pnl": pnl - 5,
                "max_drawdown": max_drawdown,
                "trade_count": 2,
                "fill_count": 4,
                "win_rate": 0.5,
                "bar_count": 100,
                "total_commission": 5,
            }
        ),
        encoding="utf-8",
    )
    with (path / "trades.csv").open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=["side", "realized_pnl"])
        writer.writeheader()
        writer.writerow({"side": "LONG", "realized_pnl": "20"})
        writer.writerow({"side": "SHORT", "realized_pnl": "-10"})


def _write_job_success(path: Path, *, symbol: str = "BTCUSDT") -> None:
    path.mkdir(parents=True, exist_ok=True)
    (path / "metrics.json").write_text(
        json.dumps(
            {
                "initial_cash": 100000,
                "final_equity": 100100,
                "total_return": 0.001,
                "gross_realized_pnl": 100,
                "net_pnl": 95,
                "max_drawdown": 0.01,
                "trade_count": 1,
                "fill_count": 2,
                "win_rate": 1.0,
                "bar_count": 3,
                "total_commission": 5,
            }
        ),
        encoding="utf-8",
    )
    with (path / "trades.csv").open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=["side", "realized_pnl"])
        writer.writeheader()
        writer.writerow({"side": "LONG", "realized_pnl": "100"})


def test_result_aggregation_writes_summary_json_md_and_failures(tmp_path):
    out = tmp_path / "outputs" / "backtests" / "vwm_batch"
    _write_success_run(out / "BINANCE_spot_BTCUSDT_1m_20260610_20260612", total_return=0.02, max_drawdown=0.01, pnl=10)
    _write_success_run(out / "BINANCE_spot_ETHUSDT_1m_20260610_20260612", total_return=0.01, max_drawdown=0.02, pnl=8)
    (out / "BINANCE_spot_SOLUSDT_1m_20260610_20260612").mkdir(parents=True)
    paths = runner.aggregate_results(out)
    for path in paths.values():
        assert Path(path).is_file()
    rows = json.loads(Path(paths["summary_json"]).read_text(encoding="utf-8"))
    by_symbol = {r["symbol"]: r for r in rows}
    assert by_symbol["BTCUSDT"]["rank_total_return"] == 1
    assert by_symbol["ETHUSDT"]["rank_total_return"] == 2
    assert by_symbol["SOLUSDT"]["status"] == "failed"
    assert by_symbol["BTCUSDT"]["long_trade_count"] == 1
    assert by_symbol["BTCUSDT"]["short_trade_count"] == 1
    with open(paths["failures_csv"], newline="", encoding="utf-8") as fh:
        failures = list(csv.DictReader(fh))
    assert failures[0]["symbol"] == "SOLUSDT"


def test_subprocess_command_uses_current_python_and_shell_false(tmp_path, monkeypatch):
    cfg = _smoke_config(tmp_path, symbols=["BTCUSDT"])
    job = runner.build_jobs(cfg)[0]
    job = runner.BatchJob(**{**runner.asdict(job), "output_dir": str(Path(cfg["output"]["root"]) / Path(job.output_dir).name)})
    config_path = Path(job.output_dir) / "config_resolved.yaml"
    config_path.parent.mkdir(parents=True)
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append((cmd, kwargs))
        return subprocess.CompletedProcess(cmd, 0, stdout="ok", stderr="")

    monkeypatch.setattr(runner.subprocess, "run", fake_run)
    completed, _elapsed = runner._run_job_subprocess(job, config_path)
    assert completed.returncode == 0
    cmd, kwargs = calls[0]
    assert isinstance(cmd, list)
    assert cmd[0] == runner.sys.executable
    assert "--_run-single-job" in cmd
    assert kwargs["shell"] is False
    assert kwargs["capture_output"] is True
    assert kwargs["text"] is True


def test_batch_smoke_success_aggregates_two_fake_subprocesses(tmp_path, monkeypatch):
    cfg = _smoke_config(tmp_path)
    monkeypatch.setattr(runner, "_bar_partitions_present", lambda **_: (True, []))
    calls = []

    def fake_job(job, config_path):
        calls.append(job.symbol)
        _write_job_success(Path(job.output_dir), symbol=job.symbol)
        return SimpleNamespace(returncode=0, stdout="ok", stderr=""), 0.1

    result = runner.run_batch_smoke(cfg, output_root=cfg["output"]["root"], run_job_fn=fake_job)
    assert result["exit_code"] == 0
    assert calls == ["BTCUSDT", "ETHUSDT"]
    out = Path(cfg["output"]["root"])
    assert (out / "summary.csv").is_file()
    assert (out / "summary.json").is_file()
    assert (out / "summary.md").is_file()
    with (out / "failures.csv").open(newline="", encoding="utf-8") as fh:
        assert list(csv.DictReader(fh)) == []
    rows = json.loads((out / "summary.json").read_text(encoding="utf-8"))
    assert sum(r["status"] == "success" for r in rows) == 2


def test_batch_smoke_failure_records_tails_and_returns_nonzero(tmp_path, monkeypatch):
    cfg = _smoke_config(tmp_path)
    monkeypatch.setattr(runner, "_bar_partitions_present", lambda **_: (True, []))

    def fake_job(job, config_path):
        if job.symbol == "BTCUSDT":
            _write_job_success(Path(job.output_dir), symbol=job.symbol)
            return SimpleNamespace(returncode=0, stdout="ok", stderr=""), 0.1
        return SimpleNamespace(returncode=9, stdout="x" * 2100, stderr="rust abort"), 0.2

    result = runner.run_batch_smoke(cfg, output_root=cfg["output"]["root"], run_job_fn=fake_job)
    assert result["exit_code"] == 10
    out = Path(cfg["output"]["root"])
    rows = json.loads((out / "summary.json").read_text(encoding="utf-8"))
    by_symbol = {r["symbol"]: r for r in rows}
    assert by_symbol["BTCUSDT"]["status"] == "success"
    assert by_symbol["ETHUSDT"]["status"] == "failed"
    assert by_symbol["ETHUSDT"]["exit_code"] == 9
    assert by_symbol["ETHUSDT"]["stderr_tail"] == "rust abort"
    assert len(by_symbol["ETHUSDT"]["stdout_tail"]) == 2000
    with (out / "failures.csv").open(newline="", encoding="utf-8") as fh:
        failures = list(csv.DictReader(fh))
    assert failures[0]["symbol"] == "ETHUSDT"
    assert failures[0]["exit_code"] == "9"


def test_fail_fast_marks_later_jobs_not_run(tmp_path, monkeypatch):
    cfg = _smoke_config(tmp_path, symbols=["BTCUSDT", "ETHUSDT", "SOLUSDT"])
    monkeypatch.setattr(runner, "_bar_partitions_present", lambda **_: (True, []))
    calls = []

    def fake_job(job, config_path):
        calls.append(job.symbol)
        if job.symbol == "BTCUSDT":
            _write_job_success(Path(job.output_dir), symbol=job.symbol)
            return SimpleNamespace(returncode=0, stdout="", stderr=""), 0.1
        return SimpleNamespace(returncode=9, stdout="", stderr="boom"), 0.2

    result = runner.run_batch_smoke(cfg, output_root=cfg["output"]["root"], run_job_fn=fake_job)
    assert result["exit_code"] == 10
    assert calls == ["BTCUSDT", "ETHUSDT"]
    rows = json.loads((Path(cfg["output"]["root"]) / "summary.json").read_text(encoding="utf-8"))
    by_symbol = {r["symbol"]: r for r in rows}
    assert by_symbol["BTCUSDT"]["status"] == "success"
    assert by_symbol["ETHUSDT"]["status"] == "failed"
    assert by_symbol["SOLUSDT"]["status"] == "not_run"
    assert by_symbol["SOLUSDT"]["error_type"] == "fail_fast_not_run"


def test_continue_on_error_runs_later_jobs(tmp_path, monkeypatch):
    cfg = _smoke_config(tmp_path, symbols=["BTCUSDT", "ETHUSDT", "SOLUSDT"])
    monkeypatch.setattr(runner, "_bar_partitions_present", lambda **_: (True, []))
    calls = []

    def fake_job(job, config_path):
        calls.append(job.symbol)
        if job.symbol == "ETHUSDT":
            return SimpleNamespace(returncode=9, stdout="", stderr="boom"), 0.2
        _write_job_success(Path(job.output_dir), symbol=job.symbol)
        return SimpleNamespace(returncode=0, stdout="", stderr=""), 0.1

    result = runner.run_batch_smoke(
        cfg,
        output_root=cfg["output"]["root"],
        fail_fast=False,
        continue_on_error=True,
        run_job_fn=fake_job,
    )
    assert result["exit_code"] == 10
    assert calls == ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
    rows = json.loads((Path(cfg["output"]["root"]) / "summary.json").read_text(encoding="utf-8"))
    assert sum(r["status"] == "success" for r in rows) == 2
    assert sum(r["status"] == "failed" for r in rows) == 1


def test_run_batch_smoke_rejects_existing_output_dir(tmp_path):
    cfg = _smoke_config(tmp_path)
    Path(cfg["output"]["root"]).mkdir(parents=True)
    with pytest.raises(FileExistsError):
        runner.run_batch_smoke(cfg, output_root=cfg["output"]["root"], run_job_fn=lambda *_: None)


def test_source_scan_has_c1a_guards():
    src = inspect.getsource(runner)
    forbidden = [
        "requests.",
        "urllib.request",
        "BinanceVisionHistoricalDownloader",
        "run_strategy.main",
        "ScheduleWakeup",
        "shutil.rmtree",
        "subprocess.Popen",
        "shell" + "=True",
    ]
    for token in forbidden:
        assert token not in src
    assert "approved smoke output roots" in src
