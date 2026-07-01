"""Parity test: self-contained VWM engine == legacy nautilus_ext VWM engine.

Runs both the new ``strategies.vwm_short.signals`` engine and the legacy
``nautilus_ext.strategies.vwm_short_signals`` engine over an identical
deterministic synthetic bar sequence and asserts the per-bar ``SignalResult``
is identical (reason, entry/exit sides, trigger price, key debug values).

Requires compiled ``nautilus_trader`` indicators — run on the remote server:

    uv run pytest strategies/vwm_short/test_parity.py -q

This test exists to certify the re-home preserved behavior. Once it passes on
the server the legacy ``nautilus_ext`` VWM chain can be deleted.
"""
from __future__ import annotations

import math

import pytest

from strategies.vwm_short.signal_types import BarInput as NewBar
from strategies.vwm_short.signals import (
    VolumeWeightedMomentumShortSignalEngine as NewEngine,
    VwmShortSignalConfig as NewConfig,
)

legacy = pytest.importorskip(
    "nautilus_ext.strategies.vwm_short_signals",
    reason="legacy nautilus_ext VWM engine not present (already deleted)",
)
from nautilus_ext.strategies.signal_types import BarInput as OldBar  # noqa: E402
from nautilus_ext.strategies.vwm_short_components import (  # noqa: E402
    VwmShortSignalConfig as OldConfig,
)


def _synthetic_bars(n: int = 400):
    """Deterministic OHLCV path with a rise then fall to exercise setups."""
    bars = []
    price = 100.0
    for i in range(n):
        # smooth oscillation + drift so VWM crosses zero repeatedly
        drift = math.sin(i / 15.0) * 3.0 + math.cos(i / 47.0) * 1.5
        close = price + drift
        high = close + 0.5 + abs(math.sin(i / 5.0))
        low = close - 0.5 - abs(math.cos(i / 7.0))
        open_ = (close + (bars[-1]["close"] if bars else close)) / 2.0
        volume = 100.0 + (i % 13) * 7.0
        bars.append(
            {
                "open": open_, "high": high, "low": low, "close": close,
                "volume": volume, "event_time_ns": i * 60_000_000_000,
                "instrument_id": "BTCUSDT.BINANCE",
            },
        )
    return bars


@pytest.mark.parametrize(
    ("mom_len", "avg_len", "atr_len", "atr_pcnt", "setup_len"),
    [(5, 20, 5, 0.5, 5), (3, 10, 7, 0.25, 3)],
)
def test_new_matches_legacy(mom_len, avg_len, atr_len, atr_pcnt, setup_len):
    new = NewEngine(NewConfig(mom_len, avg_len, atr_len, atr_pcnt, setup_len))
    old = legacy.VolumeWeightedMomentumShortSignalEngine(
        OldConfig(mom_len, avg_len, atr_len, atr_pcnt, setup_len),
    )
    pos_new = pos_old = 0
    bse_new = bse_old = 0

    for row in _synthetic_bars():
        nb = NewBar(**row)
        ob = OldBar(**row)
        if pos_new == -1:
            bse_new += 1
        if pos_old == -1:
            bse_old += 1

        r_new = new.update(nb, position=pos_new, bars_since_entry=bse_new)
        r_old = old.update(ob, position=pos_old, bars_since_entry=bse_old)

        assert r_new.reason == r_old.reason
        assert r_new.entry_side == r_old.entry_side
        assert r_new.exit_side == r_old.exit_side
        assert r_new.cancel_entry == r_old.cancel_entry
        _close(r_new.entry_price, r_old.entry_price)
        _close(r_new.debug["vwm"], r_old.debug["vwm"])
        _close(r_new.debug["atr"], r_old.debug["atr"])

        if r_new.reason == "enter_short":
            pos_new = pos_old = -1
            bse_new = bse_old = 0
        elif r_new.reason == "exit_short":
            pos_new = pos_old = 0
            bse_new = bse_old = 0


def _close(a, b, tol=1e-9):
    if a is None or b is None:
        assert a is b
        return
    assert abs(a - b) <= tol, f"{a} != {b}"
