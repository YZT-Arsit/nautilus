"""
MA5 / MA20 moving-average crossover strategy tests.

Coverage
--------
    ma5 value matches reference rolling mean
    ma20 value matches reference rolling mean
    warmup + live path equals all-on-event replay
    BUY signal on upward crossover
    SELL signal on downward crossover
    strategy code uses only public FeatureSnapshot / engine APIs
"""
from __future__ import annotations

from dataclasses import dataclass

import pytest

from nautilus_ext.features.compute.engine import SpecFeatureEngine
from nautilus_ext.features.compute.spec import FeatureSpec


# ---------------------------------------------------------------------------
# Minimal fixtures — same pattern as test_compute_features.py
# ---------------------------------------------------------------------------

@dataclass
class Bar:
    close: float = 0.0
    open: float = 0.0
    high: float = 0.0
    low: float = 0.0
    volume: float = 1.0
    instrument_id: str = "BTC/USDT"
    event_type: str = "bar"
    event_time_ns: int = 0


def _s(n: int) -> int:
    return n * 1_000_000_000


def bars(closes: list[float]) -> list[Bar]:
    return [Bar(close=c, event_time_ns=_s(i)) for i, c in enumerate(closes)]


def _rolling_mean_ref(values: list[float], window: int) -> list[float]:
    return [
        sum(values[i - window + 1: i + 1]) / window
        for i in range(window - 1, len(values))
    ]


def _crossover_signal(
    ma5: float | None,
    ma20: float | None,
    prev_ma5: float | None,
    prev_ma20: float | None,
) -> str:
    if any(v is None for v in (ma5, ma20, prev_ma5, prev_ma20)):
        return "HOLD"
    if prev_ma5 <= prev_ma20 and ma5 > ma20:
        return "BUY"
    if prev_ma5 >= prev_ma20 and ma5 < ma20:
        return "SELL"
    return "HOLD"


# ---------------------------------------------------------------------------
# Engine factory
# ---------------------------------------------------------------------------

def _ma_engine(ma5_window: int = 5, ma20_window: int = 20) -> SpecFeatureEngine:
    specs = [
        FeatureSpec(
            "ma5_close",
            input_type="bar",
            input_field="close",
            window=ma5_window,
            params={"type": "rolling_mean"},
        ),
        FeatureSpec(
            "ma20_close",
            input_type="bar",
            input_field="close",
            window=ma20_window,
            params={"type": "rolling_mean"},
        ),
    ]
    return SpecFeatureEngine(specs=specs, stamp_process_time=False)


# ===========================================================================
# Tests
# ===========================================================================

class TestMACrossover:

    # -----------------------------------------------------------------------
    # Value correctness
    # -----------------------------------------------------------------------

    def test_ma5_value_matches_reference(self):
        closes = [float(i + 1) for i in range(25)]   # 1.0 … 25.0
        engine = _ma_engine()
        snap = None
        for b in bars(closes):
            snap = engine.on_event(b)
        expected = _rolling_mean_ref(closes, 5)[-1]
        assert snap.value("ma5_close") == pytest.approx(expected)

    def test_ma20_value_matches_reference(self):
        closes = [float(i + 1) for i in range(30)]   # 1.0 … 30.0
        engine = _ma_engine()
        snap = None
        for b in bars(closes):
            snap = engine.on_event(b)
        expected = _rolling_mean_ref(closes, 20)[-1]
        assert snap.value("ma20_close") == pytest.approx(expected)

    def test_ma5_not_ready_before_window(self):
        engine = _ma_engine()
        for b in bars([100.0] * 4):
            snap = engine.on_event(b)
        assert not snap.is_ready("ma5_close")

    def test_ma20_not_ready_before_window(self):
        engine = _ma_engine()
        for b in bars([100.0] * 19):
            snap = engine.on_event(b)
        assert not snap.is_ready("ma20_close")

    def test_ma5_ready_at_window(self):
        engine = _ma_engine()
        snap = None
        for b in bars([100.0] * 5):
            snap = engine.on_event(b)
        assert snap.is_ready("ma5_close")
        assert snap.value("ma5_close") == pytest.approx(100.0)

    def test_ma20_ready_at_window(self):
        engine = _ma_engine()
        snap = None
        for b in bars([100.0] * 20):
            snap = engine.on_event(b)
        assert snap.is_ready("ma20_close")
        assert snap.value("ma20_close") == pytest.approx(100.0)

    # -----------------------------------------------------------------------
    # Warmup parity
    # -----------------------------------------------------------------------

    def test_warmup_plus_live_equals_all_on_event(self):
        closes = [float(i + 1) for i in range(30)]
        all_bars = bars(closes)

        # All-on-event path
        engine_a = _ma_engine()
        for b in all_bars:
            engine_a.on_event(b)

        # Warmup-then-live path (split at bar 20)
        engine_b = _ma_engine()
        engine_b.warmup(iter(all_bars[:20]))
        for b in all_bars[20:]:
            engine_b.on_event(b)

        assert engine_a.value("ma5_close")  == pytest.approx(engine_b.value("ma5_close"))
        assert engine_a.value("ma20_close") == pytest.approx(engine_b.value("ma20_close"))

    def test_warmup_advances_watermark(self):
        """Watermark after warmup equals the last warmup event's event_time_ns."""
        engine = _ma_engine()
        warmup_bars = bars([100.0] * 20)
        engine.warmup(iter(warmup_bars))
        # The last warmup bar is at t=19s
        assert engine.watermark_ns >= _s(19)

    # -----------------------------------------------------------------------
    # Crossover signal logic
    # -----------------------------------------------------------------------

    def test_buy_signal_on_upward_crossover(self):
        """MA5 crosses above MA20 when a price spike follows a flat period."""
        # 20 bars at 100 → MA5=MA20=100 (both equal)
        # Bar 21 at 200 → MA5 jumps, MA20 rises slowly → BUY
        closes = [100.0] * 20
        engine = _ma_engine()
        engine.warmup(iter(bars(closes)))

        prev_ma5  = engine.value("ma5_close")    # 100.0
        prev_ma20 = engine.value("ma20_close")   # 100.0

        spike_bar = Bar(close=200.0, event_time_ns=_s(20))
        snap = engine.on_event(spike_bar)

        ma5  = snap.value("ma5_close")
        ma20 = snap.value("ma20_close")

        assert ma5 > ma20, "MA5 should exceed MA20 after spike"
        assert _crossover_signal(ma5, ma20, prev_ma5, prev_ma20) == "BUY"

    def test_sell_signal_on_downward_crossover(self):
        """MA5 crosses below MA20 when a price drop follows a flat period."""
        # 20 bars at 100 → MA5=MA20=100 (both equal)
        # Bar 21 at 0 → MA5 drops hard, MA20 drops slowly → SELL
        closes = [100.0] * 20
        engine = _ma_engine()
        engine.warmup(iter(bars(closes)))

        prev_ma5  = engine.value("ma5_close")    # 100.0
        prev_ma20 = engine.value("ma20_close")   # 100.0

        drop_bar = Bar(close=0.0, event_time_ns=_s(20))
        snap = engine.on_event(drop_bar)

        ma5  = snap.value("ma5_close")
        ma20 = snap.value("ma20_close")

        assert ma5 < ma20, "MA5 should fall below MA20 after price drop"
        assert _crossover_signal(ma5, ma20, prev_ma5, prev_ma20) == "SELL"

    def test_hold_when_no_crossover(self):
        closes = [100.0] * 25
        engine = _ma_engine()
        snaps = [engine.on_event(b) for b in bars(closes)]

        prev_ma5  = snaps[-2].value("ma5_close")
        prev_ma20 = snaps[-2].value("ma20_close")
        ma5  = snaps[-1].value("ma5_close")
        ma20 = snaps[-1].value("ma20_close")

        assert _crossover_signal(ma5, ma20, prev_ma5, prev_ma20) == "HOLD"

    def test_hold_when_not_ready(self):
        assert _crossover_signal(None, None, None, None) == "HOLD"
        assert _crossover_signal(102.0, 100.0, None, 100.0) == "HOLD"

    def test_sequential_crossovers_detected(self):
        """BUY then SELL appear in the expected positions of a live sequence."""
        # Warmup at 100, then spike → hold → drop
        closes = [100.0] * 20
        engine = _ma_engine()
        engine.warmup(iter(bars(closes)))

        live_closes = [110.0] * 3 + [100.0] * 3 + [90.0] * 3 + [80.0] * 3
        signals: list[str] = []
        prev_ma5  = engine.value("ma5_close")
        prev_ma20 = engine.value("ma20_close")

        for i, c in enumerate(live_closes):
            snap = engine.on_event(Bar(close=c, event_time_ns=_s(20 + i)))
            ma5  = snap.value("ma5_close")
            ma20 = snap.value("ma20_close")
            signals.append(_crossover_signal(ma5, ma20, prev_ma5, prev_ma20))
            prev_ma5, prev_ma20 = ma5, ma20

        assert "BUY"  in signals, f"Expected BUY in {signals}"
        assert "SELL" in signals, f"Expected SELL in {signals}"
        # BUY must appear before SELL
        assert signals.index("BUY") < signals.index("SELL")

    # -----------------------------------------------------------------------
    # Public API contract
    # -----------------------------------------------------------------------

    def test_strategy_uses_only_public_api(self):
        """Verify the public API surface: value(), is_ready(), engine.value(), engine.is_ready()."""
        engine = _ma_engine()
        for b in bars([100.0] * 25):
            snap = engine.on_event(b)

        # FeatureSnapshot public API
        assert isinstance(snap.value("ma5_close"), float)
        assert isinstance(snap.value("ma20_close"), float)
        assert snap.is_ready("ma5_close") is True
        assert snap.is_ready("ma20_close") is True
        assert snap.all_ready() is True

        # SpecFeatureEngine public API
        assert isinstance(engine.value("ma5_close"), float)
        assert isinstance(engine.value("ma20_close"), float)
        assert engine.is_ready("ma5_close") is True
        assert engine.is_ready("ma20_close") is True

        # snap.value() and engine.value() agree
        assert snap.value("ma5_close") == pytest.approx(engine.value("ma5_close"))
        assert snap.value("ma20_close") == pytest.approx(engine.value("ma20_close"))

    def test_engine_value_returns_none_before_ready(self):
        engine = _ma_engine()
        for b in bars([100.0] * 4):
            engine.on_event(b)
        assert engine.value("ma5_close") is None
        assert engine.is_ready("ma5_close") is False
