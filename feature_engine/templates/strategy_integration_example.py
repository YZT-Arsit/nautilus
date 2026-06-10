"""
Strategy integration example — modular incremental Feature Engine.

Demonstrates the complete lifecycle for strategy code that uses SpecFeatureEngine:

    1.  Define FeatureSpec list (raw + derived feature chains).
    2.  Warmup from historical events via InMemoryEventProvider.
    3.  Feed live events via engine.on_event().
    4.  Read results exclusively through FeatureSnapshot / engine API.
    5.  Never touch backend internals, feature class state, or compute objects.

Rules for strategy code
-----------------------
- Import only from:
    feature_engine.compute.spec     (FeatureSpec, FeatureSnapshot, …)
    feature_engine.compute.engine   (SpecFeatureEngine)
    feature_engine.compute.adapters (adapt_*_event, InMemoryEventProvider)
- Never import from features.py, backend.py, state.py, or watermark.py.
- Never call feature.update(), feature.state_dict(), or engine._features.
- Never access engine._raw_features, engine._dep_graph, or engine._derived_names.

Security constraint
-------------------
This module only uses public market data and does NOT implement order submission.
enable_order_submit is not supported; any call to submit orders must raise
NotImplementedError.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from feature_engine.compute.adapters import (
    InMemoryEventProvider,
    adapt_bar_event,
    adapt_quote_tick_event,
)
from feature_engine.compute.engine import SpecFeatureEngine
from feature_engine.compute.spec import FeatureSnapshot, FeatureSpec


# ---------------------------------------------------------------------------
# Feature spec catalogue for this strategy
# ---------------------------------------------------------------------------

def build_specs(
    bar_window: int = 20,
    rvol_window: int = 60,
) -> list[FeatureSpec]:
    """Return the FeatureSpec list for the example strategy.

    Contains two practical derived chains:

    Chain A — quote-based spread-to-mid ratio::

        spread    (raw, quote)
        mid_price (raw, quote)
        spread_ratio = spread / mid_price (derived, ratio)

    Chain B — bar-based realized volatility::

        log_return_close  (raw, bar)
        realized_vol = rolling_std_derived(log_return_close, window=rvol_window)

    Plus several standalone raw features for signal generation.
    """
    return [
        # ── Raw bar features ────────────────────────────────────────────────
        FeatureSpec(
            name="rolling_mean_close",
            input_type="bar",
            input_field="close",
            window=bar_window,
            params={"type": "rolling_mean"},
        ),
        FeatureSpec(
            name="ewma_close",
            input_type="bar",
            input_field="close",
            window=bar_window,
            params={"type": "ewma"},
        ),
        FeatureSpec(
            name="rolling_std_close",
            input_type="bar",
            input_field="close",
            window=bar_window,
            params={"type": "rolling_std"},
        ),
        FeatureSpec(
            name="vwap_session",
            input_type="bar",
            params={"type": "vwap"},
        ),
        FeatureSpec(
            name="log_return_close",
            input_type="bar",
            input_field="close",
            params={"type": "log_return"},
        ),
        # ── Raw quote features ───────────────────────────────────────────────
        FeatureSpec(
            name="spread",
            input_type="quote",
            params={"type": "spread"},
        ),
        FeatureSpec(
            name="mid_price",
            input_type="quote",
            params={"type": "mid_price"},
        ),
        # ── Derived: Chain A (spread ratio) ─────────────────────────────────
        FeatureSpec(
            name="spread_ratio",
            input_type="derived",
            depends_on=("spread", "mid_price"),
            params={"type": "ratio"},
        ),
        # ── Derived: Chain B (realized volatility) ──────────────────────────
        FeatureSpec(
            name="realized_vol",
            input_type="derived",
            depends_on=("log_return_close",),
            window=rvol_window,
            params={"type": "rolling_std_derived"},
        ),
    ]


# ---------------------------------------------------------------------------
# Warmup helper
# ---------------------------------------------------------------------------

def warmup_from_history(
    engine: SpecFeatureEngine,
    historical_bars: list[Any],
    historical_quotes: list[Any],
) -> None:
    """Pre-heat the engine with historical bars and quotes.

    Events are interleaved by event_time_ns so watermarks advance correctly.
    All events are passed through the appropriate adapter before warmup.

    Parameters
    ----------
    engine : SpecFeatureEngine
    historical_bars : list
        Bar-like objects (BarEvent, BarMarketEvent, or duck-typed bar).
    historical_quotes : list
        Quote-like objects (QuoteTickEvent, QuoteMarketEvent, or duck-typed quote).
    """
    adapted_bars   = [adapt_bar_event(b)         for b in historical_bars]
    adapted_quotes = [adapt_quote_tick_event(q)  for q in historical_quotes]

    # Interleave by event_time_ns so watermarks advance in a realistic order.
    all_events = sorted(
        adapted_bars + adapted_quotes,
        key=lambda e: e.event_time_ns,
    )
    provider = InMemoryEventProvider(all_events)
    engine.warmup(provider.iter_events())


# ---------------------------------------------------------------------------
# Minimal signal generator (strategy-facing)
# ---------------------------------------------------------------------------

@dataclass
class Signal:
    """Output of the signal generator — no order objects, no broker calls."""
    direction: str          # "long", "short", "flat"
    confidence: float       # [0, 1]
    snapshot: FeatureSnapshot


def generate_signal(snap: FeatureSnapshot) -> Signal | None:
    """Convert a FeatureSnapshot into a trading signal.

    Returns None when the required features are not yet ready.
    This function uses ONLY the FeatureSnapshot public API — it never
    imports backend objects or accesses engine internals.

    Signal logic (illustrative — not investment advice):
    - Long when EWMA > rolling_mean (upward momentum) and spread_ratio is low.
    - Short when EWMA < rolling_mean and spread_ratio is low.
    - Flat when realized_vol is high or spread_ratio is wide.
    """
    ewma          = snap.value("ewma_close")
    mean          = snap.value("rolling_mean_close")
    spread_ratio  = snap.value("spread_ratio")
    realized_vol  = snap.value("realized_vol")

    # Require the derived features to be ready before generating signals.
    if not snap.all_ready():
        return None
    if ewma is None or mean is None or spread_ratio is None or realized_vol is None:
        return None

    # Flat when spread or vol is too high (risk filter).
    if spread_ratio > 0.01 or realized_vol > 0.02:
        return Signal(direction="flat", confidence=0.0, snapshot=snap)

    momentum = ewma - mean
    confidence = min(abs(momentum) / 1.0, 1.0)
    direction = "long" if momentum > 0 else "short"
    return Signal(direction=direction, confidence=confidence, snapshot=snap)


# ---------------------------------------------------------------------------
# Top-level integration loop (self-contained example)
# ---------------------------------------------------------------------------

def run_example() -> None:
    """Run the strategy integration example end-to-end.

    Demonstrates:
    - Engine construction from FeatureSpec list (raw + derived chains).
    - Warmup from synthetic historical events.
    - Live event loop reading FeatureSnapshot.
    - Signal generation from FeatureSnapshot only.
    - No backend internals accessed anywhere in this function.
    """
    import math

    # 1. Build engine from specs
    specs  = build_specs(bar_window=5, rvol_window=5)
    engine = SpecFeatureEngine(specs=specs, stamp_process_time=False)

    # 2. Synthetic historical data (bar + quote)
    @dataclass
    class _Bar:
        open: float; high: float; low: float; close: float; volume: float
        instrument_id: str; event_time_ns: int
        event_type: str = "bar"

    @dataclass
    class _Quote:
        bid_price: float; ask_price: float
        bid_size: float; ask_size: float
        instrument_id: str; event_time_ns: int
        event_type: str = "quote"

    ns = 1_000_000_000
    hist_bars = [
        _Bar(open=c - 0.2, high=c + 0.5, low=c - 0.5, close=c, volume=1000.0,
             instrument_id="BTC/USDT", event_time_ns=(i + 1) * ns)
        for i, c in enumerate([100.0, 100.5, 101.0, 101.3, 101.8, 102.2, 102.0])
    ]
    hist_quotes = [
        _Quote(bid_price=c - 0.05, ask_price=c + 0.05,
               bid_size=10.0, ask_size=10.0,
               instrument_id="BTC/USDT", event_time_ns=int((i + 1.5) * ns))
        for i, c in enumerate([100.25, 100.75, 101.15, 101.55, 102.0])
    ]

    # 3. Warmup
    warmup_from_history(engine, hist_bars, hist_quotes)

    # 4. Live event loop
    live_bars = [
        _Bar(open=c - 0.2, high=c + 0.5, low=c - 0.5, close=c, volume=1000.0,
             instrument_id="BTC/USDT",
             event_time_ns=(len(hist_bars) + i + 1) * ns)
        for i, c in enumerate([102.5, 103.0, 102.8])
    ]
    live_quotes = [
        _Quote(bid_price=c - 0.05, ask_price=c + 0.05,
               bid_size=10.0, ask_size=10.0,
               instrument_id="BTC/USDT",
               event_time_ns=int((len(hist_bars) + i + 1.5) * ns))
        for i, c in enumerate([102.6, 103.1, 102.9])
    ]

    live_events = sorted(
        [adapt_bar_event(b) for b in live_bars]
        + [adapt_quote_tick_event(q) for q in live_quotes],
        key=lambda e: e.event_time_ns,
    )

    signals = []
    for event in live_events:
        snap   = engine.on_event(event)
        signal = generate_signal(snap)
        if signal is not None:
            signals.append(signal)

    print(f"Processed {len(live_events)} live events, generated {len(signals)} signals.")
    for sig in signals:
        print(f"  {sig.direction:<6} confidence={sig.confidence:.2f} "
              f"ts={sig.snapshot.ts_event // 1_000_000_000}s")

    # 5. Engine diagnostics — still only public API
    print(f"\nEngine has {len(engine.feature_names())} features: {engine.feature_names()}")
    print(f"All ready: {engine.is_ready()}")


if __name__ == "__main__":
    run_example()
