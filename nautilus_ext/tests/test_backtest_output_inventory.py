"""Tests for scripts/inventory_backtest_outputs.py.

Synthetic outputs/backtests tree on tmp_path. Pure stdlib; cleanup only MOVES
(never deletes), and only confidently-superseded dirs. No network, no strategy.
"""
from __future__ import annotations

import csv
import inspect

import scripts.inventory_backtest_outputs as inv


def _mkdir(root, name, *, summary=False, eval_table=False, job=False, failures=False):
    d = root / name
    d.mkdir(parents=True)
    if summary:
        (d / "summary.json").write_text("[]")
    if eval_table:
        (d / "batch_evaluation_table.csv").write_text("Symbol\nBTCUSDT\n")
    if failures:
        (d / "failures.csv").write_text("job\nbroken\n")     # header + 1 row
    if job:
        jd = d / "BINANCE_futures_um_BTCUSDT_15m_20260301_20260531"
        jd.mkdir()
        (jd / "equity_curve.csv").write_text("equity\n100000\n")
        (jd / "trades.csv").write_text("realized_pnl\n10\n")
    return d


def _tree(tmp_path):
    root = tmp_path / "outputs" / "backtests"
    _mkdir(root, "vwm_crypto_perpetual_2026q2_15m_vol_targeted", summary=True, eval_table=True, job=True)
    _mkdir(root, "vwm_crypto_perpetual_2026q2_sizing_comparison", eval_table=True)
    _mkdir(root, "vwm_crypto_perpetual_2026q2_15m_batch", summary=True, job=True)
    _mkdir(root, "vwm_btcusdt_perpetual_matrix_7d", summary=True, job=True)     # stale -> archive
    _mkdir(root, "some_unknown_track", summary=True)                            # -> manual_review
    _mkdir(root, "weird_failed_run", summary=True, failures=True)               # other+failures -> archive_failed
    return root


def test_classification(tmp_path):
    root = _tree(tmp_path)
    rows = {r["path"].split("/")[-1]: r for r in inv.scan(root)}
    assert rows["vwm_crypto_perpetual_2026q2_15m_vol_targeted"]["recommended_action"] == "keep_delivery"
    assert rows["vwm_crypto_perpetual_2026q2_15m_vol_targeted"]["is_current_delivery"] == "yes"
    assert rows["vwm_crypto_perpetual_2026q2_sizing_comparison"]["recommended_action"] == "keep_delivery"
    assert rows["vwm_crypto_perpetual_2026q2_15m_batch"]["recommended_action"] == "keep_reference"
    assert rows["vwm_btcusdt_perpetual_matrix_7d"]["recommended_action"] == "archive_superseded"
    assert rows["vwm_btcusdt_perpetual_matrix_7d"]["is_superseded"] == "yes"
    assert rows["some_unknown_track"]["recommended_action"] == "manual_review"
    assert rows["weird_failed_run"]["recommended_action"] == "archive_failed"
    # content detection
    assert rows["vwm_crypto_perpetual_2026q2_15m_vol_targeted"]["contains_equity_curve"] == "yes"
    assert rows["vwm_crypto_perpetual_2026q2_15m_vol_targeted"]["contains_evaluation_table"] == "yes"


def test_cleanup_plan_only_archive(tmp_path):
    root = _tree(tmp_path)
    rows = inv.scan(root)
    plan = inv.cleanup_plan(rows, tmp_path / "archive")
    names = {p["old_path"].split("/")[-1] for p in plan}
    assert names == {"vwm_btcusdt_perpetual_matrix_7d", "weird_failed_run"}   # only archive_*


def test_dry_run_moves_nothing(tmp_path):
    root = _tree(tmp_path)
    inv.main(["--backtests-root", str(root), "--out", str(tmp_path / "inv.csv"),
              "--doc", str(tmp_path / "inv.md"), "--archive-root", str(tmp_path / "arch"),
              "--dry-run-cleanup"])
    assert (root / "vwm_btcusdt_perpetual_matrix_7d").is_dir()       # not moved
    assert not (tmp_path / "arch").exists()
    assert (tmp_path / "inv.csv").is_file() and (tmp_path / "inv.md").is_file()


def test_apply_cleanup_moves_and_manifests(tmp_path):
    root = _tree(tmp_path)
    rows = inv.scan(root)
    plan = inv.cleanup_plan(rows, tmp_path / "arch")
    manifest = inv.apply_cleanup(plan, tmp_path / "arch", now_iso="2026-06-26T00:00:00+00:00")
    # superseded moved, delivery untouched
    assert not (root / "vwm_btcusdt_perpetual_matrix_7d").exists()
    assert (tmp_path / "arch" / "vwm_btcusdt_perpetual_matrix_7d").is_dir()
    assert (root / "vwm_crypto_perpetual_2026q2_15m_vol_targeted").is_dir()
    moved = [m for m in manifest if m["moved"] == "yes"]
    assert len(moved) == 2 and all(m["reversible"] == "yes" for m in moved)
    inv.write_manifest(manifest, tmp_path / "arch" / "archive_manifest.csv")
    with (tmp_path / "arch" / "archive_manifest.csv").open() as fh:
        assert {"old_path", "new_path", "reason", "moved_at"} <= set(next(csv.reader(fh)))


def test_no_destructive_or_network():
    src = inspect.getsource(inv)
    for banned in ("os.remove", "rmtree", "unlink", "requests", "urllib", "http://",
                   "api_key", "/order", "leverage"):
        assert banned not in src, banned
