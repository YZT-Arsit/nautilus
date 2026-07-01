"""Locked-interface tests for results.run_uid (pure stdlib — runs anywhere)."""
from results.run_uid import build_run_uid, canonical_run_key, params_hash, window_label


def _fields(**over):
    base = {
        "strategy": "vwm", "symbol": "BTCUSDT", "exchange": "BINANCE",
        "bar_type": "1m", "start": "2026-03-01", "end": "2026-05-31",
        "fee": "nofee", "params_hash": "abc",
    }
    base.update(over)
    return base


def test_run_uid_deterministic_and_readable():
    a = build_run_uid(_fields())
    b = build_run_uid(_fields())
    assert a == b
    assert a.startswith("VWM_BTCUSDT_BINANCE_1m_20260301_20260531_nofee_")


def test_run_uid_distinguishes_params():
    assert build_run_uid(_fields(params_hash="x")) != build_run_uid(_fields(params_hash="y"))


def test_params_hash_order_independent():
    assert params_hash({"a": 1, "b": 2}) == params_hash({"b": 2, "a": 1})


def test_window_label_quarter():
    assert window_label("2026-05-31") == "2026Q2"
    assert window_label("2026-01-15") == "2026Q1"


def test_canonical_key_stable():
    assert canonical_run_key(_fields()) == canonical_run_key(_fields())
