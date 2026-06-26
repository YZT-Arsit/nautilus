"""Tests for research/evaluation_metrics.py (pure metric math).

Stdlib only; no network, no backtest, no disk. Also asserts the evaluation
modules do not import strategy / feature_engine.
"""
from __future__ import annotations

import ast
import inspect

import pytest

from research import evaluation_metrics as em


def _toplevel_import_roots(mod) -> set[str]:
    """Root package names this module actually imports (not docstring mentions)."""
    roots: set[str] = set()
    for node in ast.walk(ast.parse(inspect.getsource(mod))):
        if isinstance(node, ast.Import):
            roots.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            roots.add(node.module.split(".")[0])
    return roots


# --- primitives -------------------------------------------------------------

def test_is_finite_and_fmt_na():
    assert em.is_finite(1.5) and em.is_finite(0) and em.is_finite(-3)
    assert not em.is_finite(float("nan")) and not em.is_finite(float("inf"))
    assert not em.is_finite(True) and not em.is_finite("x") and not em.is_finite(None)
    assert em.fmt_na(None) == "NA" and em.fmt_na(float("nan")) == "NA"
    assert em.fmt_na(2.0) == 2.0


def test_safe_div_and_days_and_bar_seconds():
    assert em.safe_div(10, 4) == pytest.approx(2.5)
    assert em.safe_div(1, 0) is None and em.safe_div(None, 2) is None
    assert em.days_inclusive("2026-03-01", "2026-05-31") == 92      # inclusive
    assert em.days_inclusive("bad", "2026-05-31") is None
    assert em.bar_seconds("15m") == 900 and em.bar_seconds("1h") == 3600
    assert em.bar_seconds("5m") == 300


# --- returns ----------------------------------------------------------------

def test_annualized_and_benchmark_return():
    assert em.annualized_return(0.10, 365) == pytest.approx(0.10)
    assert em.annualized_return(-1.5, 90) is None        # total loss -> undefined
    assert em.annualized_return(0.05, 0) is None
    assert em.benchmark_return(100.0, 110.0) == pytest.approx(0.10)
    assert em.benchmark_return(0.0, 110.0) is None


def test_fee_scenarios_breakeven_and_note():
    # net 800, commission 200 -> gross 1000 on 100k
    f = em.fee_scenarios(800.0, 200.0, 100000.0, vip_ratio=0.2)
    assert f["zero"]["total_return"] == pytest.approx(0.01)          # 1000/100k
    assert f["vip"]["total_return"] == pytest.approx((1000 - 40) / 100000)
    assert f["break_even_fee_ratio_vs_current"] == pytest.approx(5.0)  # gross/commission
    assert f["zero_fee_profitable"] is True
    # gross <= 0 -> break-even 0 and signal-quality note
    g = em.fee_scenarios(-500.0, 200.0, 100000.0)
    assert g["break_even_fee_ratio_vs_current"] == 0.0
    assert "signal-quality" in g["fee_sensitivity_note"]
    assert em.fee_scenarios(1.0, 1.0, 0.0) == {}


def test_daily_stats_resamples_by_day():
    rows = [{"event_time_ns": 0, "equity": 100.0},
            {"event_time_ns": em._DAY_NS, "equity": 110.0},
            {"event_time_ns": 2 * em._DAY_NS, "equity": 99.0}]
    ds = em.daily_stats(rows)
    assert ds["best_day"] == pytest.approx(0.10)
    assert ds["worst_day"] == pytest.approx(-0.10)
    assert em.daily_stats([])["best_day"] is None


# --- risk -------------------------------------------------------------------

def test_equity_stats_and_downside_vol():
    eq = [100.0, 110.0, 105.0, 120.0]
    st = em.equity_stats(eq, bars_per_day=96)
    assert st["max_drawdown_abs"] == pytest.approx(5.0)   # 110 -> 105
    assert st["volatility"] is not None and st["sharpe"] is not None
    # needs >= 2 downside returns: 100->110 (up), 110->105 (down), 105->100 (down)
    rows = [{"equity": v} for v in (100.0, 110.0, 105.0, 100.0)]
    assert em.downside_volatility(rows, bars_per_day=96) is not None
    # only one downside return -> None
    assert em.downside_volatility(
        [{"equity": 100.0}, {"equity": 110.0}, {"equity": 105.0}], bars_per_day=96) is None


# --- trade quality ----------------------------------------------------------

def test_gross_trade_pnl_payoff_expectancy():
    trades = [{"realized_pnl": "300"}, {"realized_pnl": "-120"}, {"realized_pnl": "-50"},
              {"realized_pnl": "10"}, {"realized_pnl": "20"}]
    gr = em.gross_from_trades(trades)
    assert gr["gross_pnl"] == pytest.approx(160.0)
    assert gr["gross_profit"] == pytest.approx(330.0)
    st = em.trade_pnl_stats(trades)
    assert st["best"] == 300.0 and st["worst"] == -120.0 and st["median"] == 10.0
    assert st["max_consec_wins"] == 2 and st["max_consec_losses"] == 2
    assert em.payoff_ratio(300.0, -120.0) == pytest.approx(2.5)
    assert em.payoff_ratio(1.0, 0.0) is None
    assert em.expectancy(0.4, 300.0, -120.0) == pytest.approx(48.0)  # .4*300 + .6*-120


# --- exposure / direction / cost / relative ---------------------------------

def test_exposure_holding_direction():
    exp = em.exposure_from_positions([0.0, -1.0, -1.0, 0.0])
    assert exp["short_exposure_pct"] == pytest.approx(0.5)
    assert exp["flat_pct"] == pytest.approx(0.5) and exp["long_exposure_pct"] == 0.0
    h = em.holding_from_trades([{"entry_time_ns": 0, "exit_time_ns": 60_000_000_000}], bar_seconds=900)
    assert h["avg_holding_minutes"] == pytest.approx(1.0)
    assert em.strategy_direction_bias(0.0, 0.45) == "short"
    assert em.strategy_direction_bias(0.5, 0.0) == "long"
    assert em.benchmark_direction(0.02) == "up" and em.benchmark_direction(-0.02) == "down"
    assert em.turnover_from_trades(
        [{"quantity": 1, "entry_price": 100, "exit_price": 100}], 1000.0) == pytest.approx(0.2)


# --- safety -----------------------------------------------------------------

def test_no_network_tokens_in_source():
    # network / private-endpoint tokens must not appear as code (they are absent
    # from the docstring too, so a plain substring scan is safe here).
    src = inspect.getsource(em)
    for banned in ("requests", "urllib", "http://", "https://", "api_key", "apiKey",
                   "secret", "/account", "/order", "leverage", "websocket", "cancel"):
        assert banned not in src, banned


def test_metrics_module_imports_only_stdlib():
    # actual imports (AST), so docstring mentions of strategy/feature_engine/pyarrow
    # as "we do NOT import these" do not false-positive.
    roots = _toplevel_import_roots(em)
    for forbidden in ("strategy", "feature_engine", "data_engine", "nautilus_trader",
                      "pyarrow", "pandas", "numpy", "polars"):
        assert forbidden not in roots, forbidden
