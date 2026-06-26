"""Tests for research/backtest_artifacts.py (run_uid identity layer).

Pure stdlib. No network, no backtest, no strategy import. Verifies run_uid is
stable, sensitive to identity fields, human-readable, and never time/random based.
"""
from __future__ import annotations

import inspect

import research.backtest_artifacts as ba


def _fields(**over):
    base = {
        "strategy_name": "VWM", "strategy_version": "v1", "symbol": "BTCUSDT",
        "exchange": "BINANCE", "venue_type": "futures_um", "contract_type": "perpetual",
        "bar_type": "15m", "start": "2026-03-01", "end": "2026-05-31",
        "sizing_mode": "vol_targeted", "params_hash": "31d14fddb045",
        "data_version": "binance_vision_2026q2", "backtest_engine": "nautilus_backtest",
    }
    base.update(over)
    return base


def test_stable_hash_deterministic():
    assert ba.stable_hash("abc") == ba.stable_hash("abc")
    assert len(ba.stable_hash("abc")) == 6
    assert ba.stable_hash("abc") != ba.stable_hash("abd")


def test_run_uid_stable():
    assert ba.build_run_uid(_fields()) == ba.build_run_uid(_fields())


def test_run_uid_changes_with_symbol():
    assert ba.build_run_uid(_fields()) != ba.build_run_uid(_fields(symbol="ETHUSDT"))


def test_run_uid_changes_with_sizing_mode():
    assert ba.build_run_uid(_fields()) != ba.build_run_uid(_fields(sizing_mode="notional"))


def test_run_uid_changes_with_params_hash():
    # same prefix, different hidden field -> different suffix -> different uid
    a, b = ba.build_run_uid(_fields()), ba.build_run_uid(_fields(params_hash="deadbeef0000"))
    assert a != b
    assert a.rsplit("_", 1)[0] == b.rsplit("_", 1)[0]  # readable prefix identical


def test_run_uid_includes_strategy_symbol_window():
    uid = ba.build_run_uid(_fields())
    assert uid.startswith("VWM_BTCUSDT_BINANCE_futures_um_15m_20260301_20260531_vol_targeted_")
    assert len(uid.rsplit("_", 1)[1]) == 6  # hash suffix


def test_window_label():
    assert ba.window_label("2026-03-01", "2026-05-31") == "2026Q2"
    assert ba.window_label("2026-01-01", "2026-02-28") == "2026Q1"


def test_artifact_id():
    assert ba.artifact_id("VWM", "btcusdt", "15m", "2026Q2", "vol_targeted") == \
        "VWM_BTCUSDT_15m_2026Q2_vol_targeted"


def test_resolve_params_hash_prefers_summary():
    h, src = ba.resolve_params_hash({"params_hash": "abc123"}, "irrelevant text")
    assert h == "abc123" and src == "summary"


def test_resolve_params_hash_falls_back_to_config_text():
    h, src = ba.resolve_params_hash({}, "params:\n  mom_len: 5\n")
    assert src == "config_resolved" and len(h) == 12
    # deterministic on the same text
    assert h == ba.resolve_params_hash({}, "params:\n  mom_len: 5\n")[0]


def test_resolve_params_hash_missing():
    assert ba.resolve_params_hash(None, None) == (ba.UNKNOWN, "missing")


def test_build_identity_marks_missing_fields():
    summ = {"symbol": "BTCUSDT", "exchange": "BINANCE", "venue_type": "futures_um",
            "params_hash": "31d14fddb045"}
    ident = ba.build_identity(summ, strategy="VWM", sizing_mode="vol_targeted",
                              bar_type="15m", start="2026-03-01", end="2026-05-31")
    assert ident.run_uid == ba.build_run_uid(ident.as_key_fields())
    assert ident.params_hash == "31d14fddb045" and ident.params_hash_source == "summary"
    assert ident.contract_type == "perpetual"
    # data_version defaulted to unknown -> flagged
    assert "data_version" in ident.missing_fields
    assert ident.window_label == "2026Q2"
    assert ident.artifact_id == "VWM_BTCUSDT_15m_2026Q2_vol_targeted"


def test_contract_type_for():
    assert ba.contract_type_for("futures_um") == "perpetual"
    assert ba.contract_type_for("spot") == "spot"
    assert ba.contract_type_for("weird") == ba.UNKNOWN


def test_path_helpers():
    assert ba.pnl_filename("RU") == "RU_pnl.csv"
    assert ba.chart_filename("RU", "drawdown") == "RU_drawdown.png"
    assert ba.rel_path("a\\b\\c") == "a/b/c"
    assert ba.normalize_path("a\\b\\c") == "a/b/c"


def test_safe_relative_path():
    assert ba.safe_relative_path("/x/y/z/file.csv", "/x/y") == "z/file.csv"
    # un-relatable input falls back to the normalized path, never raises
    assert ba.safe_relative_path("a\\b", "a") in ("b", "a/b")


def test_build_artifact_record():
    rec = ba.build_artifact_record("RU", "pnl_single_csv", "pnl/RU_pnl.csv",
                                   "raw/equity_curve.csv", "raw", "ok", "2026-06-26T00:00:00+00:00")
    assert list(rec.keys()) == ba.ARTIFACT_MANIFEST_COLUMNS
    assert rec["run_uid"] == "RU" and rec["status"] == "ok"


def test_discover_run_files(tmp_path):
    (tmp_path / "equity_curve.csv").write_text("equity\n1\n")
    (tmp_path / "trades.csv").write_text("x\n")
    found = ba.discover_run_files(tmp_path)
    assert found["equity_curve"] is not None and found["trades"] is not None
    assert found["fills"] is None and found["report_json"] is None


def test_no_network_or_time_dependency():
    src = inspect.getsource(ba)
    # precise usage tokens (avoid matching prose like "random numbers" in the docstring)
    for banned in ("requests", "urllib", "http://", "https://", "api_key", "secret",
                   "/account", "/order", "leverage", "datetime.now", "time.time",
                   "import random", "random.", "os.remove", "rmtree"):
        assert banned not in src, banned
    import ast
    roots = set()
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.Import):
            roots.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            roots.add(node.module.split(".")[0])
    for forbidden in ("strategy", "feature_engine", "data_engine"):
        assert forbidden not in roots, forbidden
