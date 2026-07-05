"""Focused tests for the DoubleYourFun short engine.

Pure Python; no Nautilus, no network. A displaced-MA down/up/down "double crossing"
arms a break of the second down-cross bar's low; a break within the validity window
shorts, covered on the nearer of a reversal / trailing stop. Uses tuned short
periods (avg 3 / displace 2) so the pattern completes deterministically. Runnable
via ``pytest tests_platform -k double_your_fun``.
"""
from __future__ import annotations

from strategies.double_your_fun_short.config import DoubleYourFunShortConfig as Cfg
from strategies.double_your_fun_short.engine import DoubleYourFunShortEngine as Engine


def _cfg():
    return Cfg(avg_length=3, avg_displace=2, valid_bars1=8, valid_bars2=8,
              valid_bars3=8, trail_stop_bars=3)


def _signals(eng, closes, volume=1.0):
    out = []
    for c in closes:
        sig, reason = eng.update(c, c + 0.4, c - 0.4, c, volume)
        if sig != "HOLD":
            out.append((sig, reason))
    return out


def _double_cross_path():
    # warm (rising) -> down / up / down crosses of the displaced MA -> breakdown
    # -> rally. Arms at the 2nd down-cross (~bar 12), shorts (~bar 13), covers
    # (~bar 18).
    closes = [100 + i * 0.5 for i in range(8)]      # warm
    closes += [102, 101, 105, 106, 102, 101, 100.5]  # down / up / down
    closes += [99, 98, 97]                           # breakdown -> short
    closes += [100, 103, 106]                        # rally -> cover
    return closes


def test_double_cross_entry_and_exit():
    sigs = _signals(Engine(_cfg()), _double_cross_path())
    assert ("SELL", "enter_short") in sigs
    assert ("BUY", "exit_stop") in sigs


def test_no_trades_on_zero_volume():
    eng = Engine(_cfg())
    sigs = _signals(eng, _double_cross_path(), volume=0.0)
    assert sigs == []
    assert eng.position == 0


def test_no_entry_in_pure_uptrend():
    # A monotonic rise never produces the down/up/down pattern -> no short.
    eng = Engine(_cfg())
    sigs = _signals(eng, [100 + i for i in range(60)])
    assert all(s != "SELL" for s, _ in sigs)
    assert eng.position == 0


def test_expired_window_blocks_entry():
    # Same armed pattern, but valid_bars3=0 ages the window out before the break
    # bar, so the otherwise-valid short never fires.
    cfg = Cfg(avg_length=3, avg_displace=2, valid_bars1=8, valid_bars2=8,
              valid_bars3=0, trail_stop_bars=3)
    sigs = _signals(Engine(cfg), _double_cross_path())
    assert all(s != "SELL" for s, _ in sigs)
