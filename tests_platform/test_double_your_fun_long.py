"""Focused tests for the DoubleYourFun long engine.

Pure Python; no Nautilus, no network. A displaced-MA up/down/up "double crossing"
arms a break of the second up-cross bar's high; a break within the validity window
buys, sold on the farther of a reversal / trailing stop. Uses tuned short periods
(avg 3 / displace 2) so the pattern completes deterministically. Runnable via
``pytest tests_platform -k double_your_fun``.
"""
from __future__ import annotations

from strategies.double_your_fun_long.config import DoubleYourFunLongConfig as Cfg
from strategies.double_your_fun_long.engine import DoubleYourFunLongEngine as Engine


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
    # warm (falling) -> up / down / up crosses of the displaced MA -> breakout up
    # -> selloff. Arms at the 2nd up-cross (~bar 12), longs (~bar 13), sells
    # (~bar 18).
    closes = [106 - i * 0.5 for i in range(8)]        # warm
    closes += [104, 105, 101, 100, 104, 105, 105.5]    # up / down / up
    closes += [107, 108, 109]                          # breakout -> long
    closes += [106, 103, 100]                          # selloff -> sell
    return closes


def test_double_cross_entry_and_exit():
    sigs = _signals(Engine(_cfg()), _double_cross_path())
    assert ("BUY", "enter_long") in sigs
    assert ("SELL", "exit_stop") in sigs


def test_no_trades_on_zero_volume():
    eng = Engine(_cfg())
    sigs = _signals(eng, _double_cross_path(), volume=0.0)
    assert sigs == []
    assert eng.position == 0


def test_no_entry_in_pure_downtrend():
    # A monotonic fall never produces the up/down/up pattern -> no long.
    eng = Engine(_cfg())
    sigs = _signals(eng, [160 - i for i in range(60)])
    assert all(s != "BUY" for s, _ in sigs)
    assert eng.position == 0


def test_expired_window_blocks_entry():
    # Same armed pattern, but valid_bars3=0 ages the window out before the break
    # bar, so the otherwise-valid long never fires.
    cfg = Cfg(avg_length=3, avg_displace=2, valid_bars1=8, valid_bars2=8,
              valid_bars3=0, trail_stop_bars=3)
    sigs = _signals(Engine(cfg), _double_cross_path())
    assert all(s != "BUY" for s, _ in sigs)
