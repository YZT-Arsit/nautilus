"""Focused tests for the Turtle trading system.

The default synthetic data path is a single rise->fall built for MA-crossover, so
a warmed 55-bar Donchian breakout system correctly never trades on it. These
tests instead drive the pure :class:`TurtleTraderEngine` with crafted OHLC paths
that deterministically exercise each branch — breakout entry, N-based sizing,
pyramiding, the 2N stop + last-profitable-trade filter, the failsafe breakout,
and the trailing exit — plus the framework rich-plan execution wiring.

Pure Python; no Nautilus, no network. Runnable locally and on the server via
``pytest tests_platform -k turtle``.
"""
from __future__ import annotations

from strategies.turtle_trader.config import TurtleTraderConfig
from strategies.turtle_trader.engine import TurtleTraderEngine
from strategy_framework.backends.nautilus_simulation import IntentFillSimulator
from strategy_framework.execution.intents import PlannedSignal, TradeAction
from strategy_framework.execution.signal_policy import plan_to_intents


def _flat_bar(price: float) -> tuple[float, float, float, float]:
    """A zero-range bar (open=high=low=close) at ``price``."""
    return (price, price, price, price)


def _feed_flat(engine: TurtleTraderEngine, prices: list[float]) -> list:
    """Feed a list of flat bars; return the list of per-bar action lists."""
    out = []
    for p in prices:
        _, actions, _ = engine.update(*_flat_bar(p))
        out.append(actions)
    return out


# -- sizing -----------------------------------------------------------------

def test_turtle_units_are_risk_over_n_int_part():
    cfg = TurtleTraderConfig(account_equity=100_000, risk_ratio=1.0,
                             contract_unit=1.0, big_point_value=1.0)
    eng = TurtleTraderEngine(cfg)
    # risk budget = 100000 * 1% = 1000; N=2 -> 500 units (IntPart).
    assert eng._turtle_units(2.0) == 500.0
    # N=3 -> 333.33 -> IntPart 333.
    assert eng._turtle_units(3.0) == 333.0
    # non-positive / missing N -> no size.
    assert eng._turtle_units(0.0) == 0.0
    assert eng._turtle_units(None) == 0.0


def test_max_units_cap_bounds_sizing():
    cfg = TurtleTraderConfig(max_units_per_entry=5)
    eng = TurtleTraderEngine(cfg)
    assert eng._turtle_units(0.0001) == 5.0  # tiny N would explode; cap holds


# -- entry + pyramiding -----------------------------------------------------

def _staircase_engine():
    """Engine primed so a rising staircase triggers a long breakout + adds.

    breakout_len=5, atr_length=5, failsafe far away, unit size = 1 for simple
    accounting. account_equity/risk chosen so TurtleUnits == 1.
    """
    cfg = TurtleTraderConfig(
        breakout_len=5, failsafe_len=50, trailing_exit_len=5, atr_length=5,
        n_entries=3, last_profitable_trade_filter=False,
        account_equity=1000.0, risk_ratio=100.0, contract_unit=1.0,
        big_point_value=1.0, min_point=0.0,
    )
    return TurtleTraderEngine(cfg)


def test_long_breakout_entry_and_pyramiding():
    eng = _staircase_engine()
    # Warm the channels/ATR flat at 100 for > atr_length & breakout_len bars.
    _feed_flat(eng, [100.0] * 10)
    n = eng._atr  # ~0 after flat; force a non-trivial N by injecting volatility
    assert eng.position == 0

    # Introduce range so N > 0: a couple of wider bars.
    for _ in range(6):
        eng.update(100.0, 101.0, 99.0, 100.0)  # TR ~2 -> N rises
    assert eng._atr and eng._atr > 0

    # Now break the 5-bar high (prior highs are 101) with a big up bar.
    _, actions, reason = eng.update(102.0, 110.0, 102.0, 109.0)
    assert eng.position == 1
    assert any(a.side == "BUY" for a in actions)
    assert "breakout" in reason
    units_after_entry = eng.units
    assert units_after_entry >= 1
    assert eng.current_entries == 1

    # A further push up by >= 0.5N should pyramid at least one add.
    n = eng._atr
    push_to = eng.pre_entry_price + 5 * n  # comfortably several 0.5N steps
    _, add_actions, _ = eng.update(push_to, push_to, push_to - 1, push_to)
    assert eng.current_entries > 1                      # pyramided
    assert eng.current_entries <= eng.cfg.n_entries     # capped at n_entries
    assert eng.units > units_after_entry
    assert all(a.side == "BUY" for a in add_actions)


def test_two_n_stop_closes_and_sets_lpt_filter():
    # Isolate the 2N hard stop from the (tighter) trailing exit by making the
    # trailing-exit channel never ready (trailing_exit_len far exceeds the bars
    # fed), so only the disaster stop can fire.
    cfg = TurtleTraderConfig(
        breakout_len=5, failsafe_len=50, trailing_exit_len=1000, atr_length=5,
        n_entries=3, last_profitable_trade_filter=False, min_point=0.0,
        account_equity=1000.0, risk_ratio=100.0,
    )
    eng = TurtleTraderEngine(cfg)
    for _ in range(6):
        eng.update(100.0, 101.0, 99.0, 100.0)
    for _ in range(6):
        eng.update(100.0, 101.0, 99.0, 100.0)
    eng.update(102.0, 110.0, 102.0, 109.0)  # long entry (breaks the 5-bar high)
    assert eng.position == 1
    entry = eng.pre_entry_price
    n = eng._atr

    # A bar whose low pierces entry-2N (and no add this bar) -> hard stop, flat.
    stop_low = entry - 2 * n - 1
    _, actions, reason = eng.update(entry - 0.1, entry, stop_low, entry - 0.1)
    assert eng.position == 0
    assert any(a.close_all for a in actions)
    assert "stop" in reason
    assert eng.pre_breakout_failure is True  # last breakout failed -> filter armed


def test_lpt_filter_blocks_breakout_until_failure():
    # With the filter ON and no prior failure, a short-period breakout is blocked;
    # the failsafe (long-period) breakout is what opens instead.
    cfg = TurtleTraderConfig(
        breakout_len=5, failsafe_len=8, trailing_exit_len=5, atr_length=5,
        last_profitable_trade_filter=True, min_point=0.0,
        account_equity=1000.0, risk_ratio=100.0,
    )
    eng = TurtleTraderEngine(cfg)
    for _ in range(10):
        eng.update(100.0, 101.0, 99.0, 100.0)  # warm, build N and channels
    # Break the 5-bar high but NOT the 8-bar failsafe high yet is impossible here
    # (both are 101); a break of 101 satisfies both. With filter armed=False, the
    # short-period block is skipped, so the failsafe branch takes the entry.
    _, actions, reason = eng.update(102.0, 105.0, 102.0, 104.0)
    assert eng.position == 1
    assert "failsafe" in reason  # filter blocked the plain breakout; failsafe fired


# -- trailing exit + short side ---------------------------------------------

def test_short_breakout_and_trailing_exit():
    cfg = TurtleTraderConfig(
        breakout_len=5, failsafe_len=50, trailing_exit_len=5, atr_length=5,
        last_profitable_trade_filter=False, min_point=0.0,
        account_equity=1000.0, risk_ratio=100.0,
    )
    eng = TurtleTraderEngine(cfg)
    for _ in range(6):
        eng.update(100.0, 101.0, 99.0, 100.0)
    for _ in range(6):
        eng.update(100.0, 101.0, 99.0, 100.0)
    # Break the 5-bar low downward -> short entry.
    _, actions, reason = eng.update(98.0, 98.0, 90.0, 91.0)
    assert eng.position == -1
    assert any(a.side == "SELL" for a in actions)

    # Feed a few bars so the trailing-exit channel (highs of prior bars) sits
    # above, then a bar whose high exceeds it -> trailing exit closes the short.
    for _ in range(5):
        eng.update(91.0, 92.0, 90.0, 91.0)
    _, exit_actions, reason = eng.update(93.0, 120.0, 93.0, 119.0)
    assert eng.position == 0
    assert any(a.close_all for a in exit_actions)
    assert "trailing_exit" in reason


# -- framework wiring (rich plan -> intents -> simulated fills) ---------------

class _Ev:
    def __init__(self, price):
        self.instrument_id = "BTCUSDT.BINANCE"
        self.event_time_ns = 1
        self.close = price
        self.open = self.high = self.low = price


def test_plan_to_intents_and_simulated_fill_multi_unit():
    # A rich plan with two BUY adds then a close_all must produce 3 fills and a
    # flat position in the dependency-free simulator (pyramiding round-trip).
    sim = IntentFillSimulator(allow_short=True)
    plan = [
        TradeAction("BUY", 2.0, "enter", fill_price=100.0),
        TradeAction("BUY", 2.0, "add", fill_price=102.0),
    ]
    for intent in plan_to_intents(plan, _Ev(100.0)):
        sim.on_intent(intent, _Ev(100.0))
    rep = sim.report()
    assert rep.total_fills == 2
    # avg price of 4 units = (2*100 + 2*102)/4 = 101
    pos = rep.positions[0]
    assert pos.quantity == 4.0
    assert abs(pos.avg_price - 101.0) < 1e-9

    # Now flatten via a close_all action at 110 -> realized pnl = 4*(110-101)=36.
    close = [TradeAction("SELL", 0.0, "exit", close_all=True, fill_price=110.0)]
    for intent in plan_to_intents(close, _Ev(110.0)):
        sim.on_intent(intent, _Ev(110.0))
    rep = sim.report()
    assert not rep.positions  # flat
    assert abs(rep.realized_pnl - 36.0) < 1e-9


def test_planned_signal_is_backward_compatible_string():
    sig = PlannedSignal("BUY", (TradeAction("BUY", 1.0),))
    assert sig == "BUY"                 # behaves as its label
    assert {sig: 1}[sig] == 1           # hashable like a str
    assert len(sig.actions) == 1        # but carries the plan
    hold = PlannedSignal("HOLD")
    assert hold == "HOLD" and hold.actions == ()
