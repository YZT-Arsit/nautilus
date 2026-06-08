#!/usr/bin/env python3
"""
Feature Engine Demo — compact event-by-event table.

Prints one row per market event showing:
    event_time_s  event_type  updated_features  selected values  signal

Demonstrates both practical derived chains:
    Chain A:  spread, mid_price  →  spread_ratio   (quote-driven, ratio)
    Chain B:  log_return_close   →  realized_vol   (bar-driven,  rolling_std_derived)

Uses only the public SpecFeatureEngine / FeatureSnapshot API.
No backend internals are accessed.

Usage
-----
    python -m scripts.run_feature_engine_demo
    python -m scripts.run_feature_engine_demo --events 30 --bar-window 5 --rvol-window 5
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from typing import Any


# ---------------------------------------------------------------------------
# Feature spec catalogue
# ---------------------------------------------------------------------------

def _build_specs(bar_window: int, rvol_window: int):
    from nautilus_ext.features.compute.spec import FeatureSpec

    return [
        # Raw bar
        FeatureSpec("rolling_sum_vol",   input_type="bar",   input_field="volume",
                    window=bar_window, params={"type": "rolling_sum"}),
        FeatureSpec("rolling_mean_close",input_type="bar",   input_field="close",
                    window=bar_window, params={"type": "rolling_mean"}),
        FeatureSpec("log_return_close",  input_type="bar",   input_field="close",
                    params={"type": "log_return"}),
        # Raw quote
        FeatureSpec("spread",            input_type="quote", params={"type": "spread"}),
        FeatureSpec("mid_price",         input_type="quote", params={"type": "mid_price"}),
        # Derived — Chain A: spread_ratio = spread / mid_price
        FeatureSpec("spread_ratio",      input_type="derived",
                    depends_on=("spread", "mid_price"),
                    params={"type": "ratio"}),
        # Derived — Chain B: realized_vol = rolling_std(log_return, window=rvol_window)
        FeatureSpec("realized_vol",      input_type="derived",
                    depends_on=("log_return_close",),
                    window=rvol_window,
                    params={"type": "rolling_std_derived"}),
    ]


# ---------------------------------------------------------------------------
# Minimal signal generator (snapshot-only API)
# ---------------------------------------------------------------------------

def _signal(snap) -> str:
    """Return a brief signal label from a FeatureSnapshot."""
    if not snap.all_ready():
        return "—"
    sr   = snap.value("spread_ratio")
    rvol = snap.value("realized_vol")
    ewma = snap.value("rolling_mean_close")
    if sr is None or rvol is None or ewma is None:
        return "—"
    if sr > 0.002 or rvol > 0.005:
        return "FLAT(risk)"
    mid = snap.value("mid_price")
    if mid is None:
        return "—"
    if mid > ewma:
        return "LONG"
    if mid < ewma:
        return "SHORT"
    return "FLAT"


# ---------------------------------------------------------------------------
# Synthetic event factories
# ---------------------------------------------------------------------------

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


def _make_events(n_bars: int, n_quotes: int) -> list[Any]:
    ns = 1_000_000_000
    prices = [100.0 + i * 0.15 + (i % 5) * 0.03 for i in range(n_bars)]
    bars = [
        _Bar(open=c - 0.1, high=c + 0.3, low=c - 0.3, close=c,
             volume=500.0 + i * 10,
             instrument_id="BTC/USDT",
             event_time_ns=(i + 1) * ns)
        for i, c in enumerate(prices)
    ]
    mids = [100.0 + i * 0.12 for i in range(n_quotes)]
    quotes = [
        _Quote(bid_price=m - 0.05, ask_price=m + 0.05,
               bid_size=10.0, ask_size=10.0,
               instrument_id="BTC/USDT",
               event_time_ns=int((i + 1.5) * ns))
        for i, m in enumerate(mids)
    ]
    all_events = sorted(bars + quotes, key=lambda e: e.event_time_ns)
    return all_events


# ---------------------------------------------------------------------------
# Table printer
# ---------------------------------------------------------------------------

_DISPLAY_FEATURES = ["spread_ratio", "realized_vol", "rolling_mean_close", "mid_price"]
_COL_W = 12


def _hdr() -> str:
    cols = (
        f"{'time(s)':<8} {'type':<7} {'updated':<30}"
        + "".join(f"{n[:_COL_W]:>{_COL_W}}" for n in _DISPLAY_FEATURES)
        + f"  {'signal':<12}"
    )
    return cols


def _row(snap, event) -> str:
    t_s = event.event_time_ns // 1_000_000_000
    etype = event.event_type[:6]
    updated = ",".join(snap.updated_names()[:4]) or "-"
    vals = []
    for n in _DISPLAY_FEATURES:
        fv = snap.get(n)
        if fv is None or fv.value is None:
            vals.append(f"{'—':>{_COL_W}}")
        else:
            vals.append(f"{fv.value:>{_COL_W}.5f}")
    sig = _signal(snap)
    return (
        f"{t_s:<8} {etype:<7} {updated:<30}"
        + "".join(vals)
        + f"  {sig:<12}"
    )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Feature Engine demo — compact event table",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--events",     type=int, default=20,
                        help="number of bar events to generate (default: 20)")
    parser.add_argument("--bar-window", type=int, default=5,
                        help="rolling window for bar features (default: 5)")
    parser.add_argument("--rvol-window", type=int, default=5,
                        help="realized_vol window (default: 5)")
    parser.add_argument("--warmup",     type=int, default=0,
                        help="warmup events (default: 0 — no separate warmup)")
    args = parser.parse_args()

    from nautilus_ext.features.compute.adapters import adapt_bar_event, adapt_quote_tick_event
    from nautilus_ext.features.compute.engine import SpecFeatureEngine

    specs  = _build_specs(args.bar_window, args.rvol_window)
    engine = SpecFeatureEngine(specs=specs, stamp_process_time=False, profile=True)

    n_bars   = args.events
    n_quotes = args.events          # interleave bar + quote
    all_events = _make_events(n_bars, n_quotes)

    # Optional warmup
    n_warmup = args.warmup or 0
    if n_warmup > 0:
        warmup_events = all_events[:n_warmup]
        live_events   = all_events[n_warmup:]
        warmup_adapted = [
            adapt_bar_event(e) if e.event_type == "bar" else adapt_quote_tick_event(e)
            for e in warmup_events
        ]
        engine.warmup(iter(warmup_adapted))
        print(f"Warmed up on {len(warmup_adapted)} events.")
    else:
        live_events = all_events

    print()
    print(_hdr())
    print("-" * (8 + 1 + 7 + 1 + 30 + _COL_W * len(_DISPLAY_FEATURES) + 14))

    n_signals = 0
    for raw_event in live_events:
        if raw_event.event_type == "bar":
            adapted = adapt_bar_event(raw_event)
        else:
            adapted = adapt_quote_tick_event(raw_event)
        snap = engine.on_event(adapted)
        sig  = _signal(snap)
        if sig not in ("—", "FLAT(risk)") or snap.all_ready():
            n_signals += 1 if sig not in ("—",) else 0
        print(_row(snap, raw_event))

    print()
    print(f"Processed {len(live_events)} live events.")

    # Health summary
    health = engine.health_summary(stale_threshold_ns=5_000_000_000)
    print(f"\nHealth: {health['n_ready']}/{health['n_features']} features ready.")
    if health.get("not_ready_features"):
        print(f"  Not ready: {health['not_ready_features']}")
    if health.get("stale_features"):
        print(f"  Stale:     {health['stale_features']}")

    # Per-feature update counts
    print("\nPer-feature update counts:")
    for name, h in health.get("features", {}).items():
        print(
            f"  {name:<25}  updated={h['update_count']:>4}"
            f"  dep_not_ready={h['dependency_not_ready_count']:>3}"
            f"  last={h['last_status'] or '-'}"
        )


if __name__ == "__main__":
    main()
