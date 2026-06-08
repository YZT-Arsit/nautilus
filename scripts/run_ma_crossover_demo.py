#!/usr/bin/env python3
"""
MA5 / MA20 Moving-Average Crossover Strategy Demo.

Demonstrates:
  - FeatureSpec-based rolling_mean feature for MA5 and MA20
  - Warmup with historical events (engine pre-heated, no live penalty)
  - Live event loop with crossover signal detection
  - Only public SpecFeatureEngine / FeatureSnapshot APIs used

Chains
------
    ma5_close  = rolling_mean(bar.close, window=5)
    ma20_close = rolling_mean(bar.close, window=20)

Signal rules
------------
    BUY   : MA5 crosses above MA20  (prev_ma5 <= prev_ma20 AND curr_ma5 > curr_ma20)
    SELL  : MA5 crosses below MA20  (prev_ma5 >= prev_ma20 AND curr_ma5 < curr_ma20)
    HOLD  : otherwise

Usage
-----
    python -m scripts.run_ma_crossover_demo
    python -m scripts.run_ma_crossover_demo --warmup 20 --live 20 --ma5-window 5 --ma20-window 20
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass


# ---------------------------------------------------------------------------
# Feature spec factory
# ---------------------------------------------------------------------------

def _build_specs(ma5_window: int, ma20_window: int):
    from nautilus_ext.features.compute.spec import FeatureSpec

    return [
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


# ---------------------------------------------------------------------------
# Signal logic (uses only public snapshot API)
# ---------------------------------------------------------------------------

def _crossover_signal(
    ma5: float | None,
    ma20: float | None,
    prev_ma5: float | None,
    prev_ma20: float | None,
) -> str:
    """Return BUY / SELL / HOLD based on MA crossover state."""
    if any(v is None for v in (ma5, ma20, prev_ma5, prev_ma20)):
        return "HOLD"
    if prev_ma5 <= prev_ma20 and ma5 > ma20:
        return "BUY"
    if prev_ma5 >= prev_ma20 and ma5 < ma20:
        return "SELL"
    return "HOLD"


# ---------------------------------------------------------------------------
# Minimal bar event (matches engine's input_type="bar" routing)
# ---------------------------------------------------------------------------

@dataclass
class _Bar:
    close: float
    open: float
    high: float
    low: float
    volume: float
    instrument_id: str
    event_time_ns: int
    event_type: str = "bar"


def _make_bars(closes: list[float], *, start_ns: int = 0, step_ns: int = 1_000_000_000) -> list[_Bar]:
    return [
        _Bar(
            close=c,
            open=c - 0.5,
            high=c + 1.0,
            low=c - 1.0,
            volume=100.0,
            instrument_id="BTC/USDT",
            event_time_ns=start_ns + i * step_ns,
        )
        for i, c in enumerate(closes)
    ]


# ---------------------------------------------------------------------------
# Table formatting
# ---------------------------------------------------------------------------

_HDR = f"{'time(s)':>8}  {'close':>8}  {'ma5':>10}  {'ma20':>10}  {'signal':<8}"
_SEP = "-" * len(_HDR)


def _fmt_f(v: float | None, width: int = 10) -> str:
    return f"{v:>{width}.4f}" if v is not None else f"{'—':>{width}}"


def _row(t_ns: int, close: float, ma5: float | None, ma20: float | None, signal: str) -> str:
    t_s = t_ns // 1_000_000_000
    return (
        f"{t_s:>8}  {close:>8.2f}  {_fmt_f(ma5)}  {_fmt_f(ma20)}  {signal:<8}"
    )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="MA5/MA20 crossover demo",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--warmup",      type=int, default=20,
                        help="number of historical bars for warmup (default: 20)")
    parser.add_argument("--live",        type=int, default=20,
                        help="number of live bars to process (default: 20)")
    parser.add_argument("--ma5-window",  type=int, default=5,
                        help="MA5 rolling window (default: 5)")
    parser.add_argument("--ma20-window", type=int, default=20,
                        help="MA20 rolling window (default: 20)")
    args = parser.parse_args()

    from nautilus_ext.features.compute.engine import SpecFeatureEngine

    specs = _build_specs(args.ma5_window, args.ma20_window)
    engine = SpecFeatureEngine(specs=specs, stamp_process_time=False)

    # Price series: flat warmup → sharp rise (BUY) → hold → drop (SELL) → stable
    warmup_closes = [100.0] * args.warmup
    live_closes = (
        [110.0] * 3
        + [100.0] * 3
        + [90.0] * 3
        + [80.0] * 3
        + [80.0] * max(0, args.live - 12)
    )[: args.live]

    step_ns = 1_000_000_000
    warmup_bars = _make_bars(warmup_closes, start_ns=0, step_ns=step_ns)
    live_bars   = _make_bars(
        live_closes,
        start_ns=len(warmup_bars) * step_ns,
        step_ns=step_ns,
    )

    # Warmup (no live overhead; watermarks advance correctly)
    engine.warmup(iter(warmup_bars))
    print(f"Warmed up on {len(warmup_bars)} bars.")
    print(f"  ma5_close  ready: {engine.is_ready('ma5_close')}")
    print(f"  ma20_close ready: {engine.is_ready('ma20_close')}")
    print()

    # Live loop — track previous MA values for crossover detection
    print(_HDR)
    print(_SEP)

    prev_ma5:  float | None = engine.value("ma5_close")
    prev_ma20: float | None = engine.value("ma20_close")

    for bar in live_bars:
        snap = engine.on_event(bar)

        ma5  = snap.value("ma5_close")
        ma20 = snap.value("ma20_close")

        signal = _crossover_signal(ma5, ma20, prev_ma5, prev_ma20)
        print(_row(bar.event_time_ns, bar.close, ma5, ma20, signal))

        prev_ma5  = ma5
        prev_ma20 = ma20

    print()
    print(f"Processed {len(live_bars)} live bars.")
    print(f"Final  ma5_close  = {engine.value('ma5_close'):.4f}")
    print(f"Final  ma20_close = {engine.value('ma20_close'):.4f}")


if __name__ == "__main__":
    main()
