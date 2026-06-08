#!/usr/bin/env python3
"""
Lightweight benchmark for the SpecFeatureEngine incremental hot path.

Usage
-----
    python scripts/benchmark_feature_engine.py [--events N] [--features N] [--window N]

Examples
--------
    # default: 100 000 events, 20 features, window 100
    python scripts/benchmark_feature_engine.py

    # stress: 100 features, large window
    python scripts/benchmark_feature_engine.py --events 100000 --features 100 --window 1000

Metrics reported
----------------
    total elapsed     total wall-clock time for all on_event() calls
    avg on_event      mean latency per call (µs)
    p50 / p95 / p99   percentile latencies (µs)
    events/sec        throughput
    feature·events/s  throughput × n_features (total incremental updates/sec)

Interpretation
--------------
  - avg on_event scales linearly with --features (O(n_features) per event by design).
  - Increasing --window has NO effect on steady-state latency; the ring buffer is
    fixed-size (O(1) push regardless of window size).  If you see regression here,
    something on the hot path has grown unbounded.
  - p99 < 100 µs for 20 features on a quiet machine is a reasonable baseline;
    actual values depend heavily on CPU, Python version, and OS scheduler.

IMPORTANT: Do NOT use these numbers as hard CI pass/fail thresholds.
  They are machine- and load-dependent.  Use for relative profiling — compare
  runs at different --features counts to verify linearity, or compare before/after
  a hot-path change to detect regressions.
"""
from __future__ import annotations

import argparse
import time
from dataclasses import dataclass, field


# ---------------------------------------------------------------------------
# Synthetic event
# ---------------------------------------------------------------------------

@dataclass
class _Bar:
    open: float = 100.0
    high: float = 101.0
    low: float = 99.0
    close: float = 100.5
    volume: float = 1000.0
    event_type: str = "bar"
    instrument_id: str = "BENCH/USDT"
    event_time_ns: int = 0
    receive_time_ns: int | None = None


# ---------------------------------------------------------------------------
# Spec / event factories
# ---------------------------------------------------------------------------

def _make_specs(n_features: int, window: int):
    from nautilus_ext.features.compute.spec import FeatureSpec

    # Cycle through feature types that work reliably with bar events
    _ENTRIES: list[tuple[str, str | None]] = [
        ("rolling_mean",    "close"),
        ("rolling_std",     "close"),
        ("rolling_min",     "low"),
        ("rolling_max",     "high"),
        ("rolling_sum",     "volume"),   # needs input_field
        ("rolling_volume_sum", None),    # uses _DEFAULT_FIELD="volume"
        ("ewma",            "close"),
        ("simple_return",   "close"),
        ("log_return",      "close"),
        ("vwap",            None),
    ]
    specs = []
    for i in range(n_features):
        ftype, ifield = _ENTRIES[i % len(_ENTRIES)]
        specs.append(FeatureSpec(
            name=f"f{i}_{ftype}",
            input_type="bar",
            input_field=ifield,
            window=window,
            params={"type": ftype},   # explicit type so name prefix doesn't matter
        ))
    return specs


def _make_events(n: int, start_ns: int = 1_000_000_000) -> list[_Bar]:
    events: list[_Bar] = []
    base = 100.0
    for i in range(n):
        price = base + (i % 20) * 0.05
        events.append(_Bar(
            open=price - 0.1,
            high=price + 0.5,
            low=price - 0.5,
            close=price,
            volume=float(500 + i % 1000),
            event_time_ns=start_ns + i * 1_000_000_000,
        ))
    return events


# ---------------------------------------------------------------------------
# Stats helpers
# ---------------------------------------------------------------------------

def _pct(sorted_ns: list[int], p: float) -> float:
    """Return the p-th percentile of a pre-sorted list of ns values, in µs."""
    idx = min(int(len(sorted_ns) * p), len(sorted_ns) - 1)
    return sorted_ns[idx] / 1_000.0


def _fmt(label: str, value: str) -> str:
    return f"  {label:<24}{value:>14}"


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Benchmark SpecFeatureEngine incremental hot path",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--events",   type=int, default=100_000,
                        help="number of live events to process (default: 100 000)")
    parser.add_argument("--features", type=int, default=20,
                        help="number of features registered in the engine (default: 20)")
    parser.add_argument("--window",   type=int, default=100,
                        help="rolling-window lookback size (default: 100)")
    parser.add_argument("--warmup",   type=int, default=0,
                        help="warmup events before benchmarking "
                             "(default: same as --window)")
    parser.add_argument("--profile", action="store_true",
                        help="enable engine profiling and print summary")
    args = parser.parse_args()

    n_events  = args.events
    n_feats   = args.features
    window    = args.window
    n_warmup  = args.warmup if args.warmup > 0 else window

    from nautilus_ext.features.compute.engine import SpecFeatureEngine

    print()
    print("  SpecFeatureEngine Benchmark")
    print("  " + "-" * 36)
    print(_fmt("events:",   f"{n_events:,}"))
    print(_fmt("features:", f"{n_feats}"))
    print(_fmt("window:",   f"{window}"))
    print(_fmt("warmup:",   f"{n_warmup}"))

    # --- build ---
    t0 = time.perf_counter_ns()
    specs  = _make_specs(n_feats, window)
    engine = SpecFeatureEngine(specs, stamp_process_time=False,
                               profile=args.profile)
    build_ms = (time.perf_counter_ns() - t0) / 1e6
    print(_fmt("build time:", f"{build_ms:.1f} ms"))

    # --- events ---
    warmup_events = _make_events(n_warmup, start_ns=0)
    live_events   = _make_events(n_events, start_ns=n_warmup * 1_000_000_000)

    engine.warmup(warmup_events)

    # --- hot-path benchmark ---
    lats: list[int] = []
    t_start = time.perf_counter_ns()
    for ev in live_events:
        t_ev = time.perf_counter_ns()
        engine.on_event(ev)
        lats.append(time.perf_counter_ns() - t_ev)
    t_total = time.perf_counter_ns() - t_start

    total_s  = t_total / 1e9
    avg_us   = (t_total / n_events) / 1e3
    ups      = n_events / total_s
    feat_eps = ups * n_feats

    lats.sort()
    p50 = _pct(lats, 0.50)
    p95 = _pct(lats, 0.95)
    p99 = _pct(lats, 0.99)

    print()
    print("  Results")
    print("  " + "-" * 36)
    print(_fmt("total elapsed:",   f"{total_s * 1000:.1f} ms"))
    print(_fmt("avg on_event:",    f"{avg_us:.2f} µs"))
    print(_fmt("p50 on_event:",    f"{p50:.2f} µs"))
    print(_fmt("p95 on_event:",    f"{p95:.2f} µs"))
    print(_fmt("p99 on_event:",    f"{p99:.2f} µs"))
    print(_fmt("events/sec:",      f"{ups:,.0f}"))
    print(_fmt("feature·ev/sec:",  f"{feat_eps:,.0f}"))
    print()

    if args.profile:
        summary = engine.profile_summary()
        if summary.get("profile"):
            print("  Profile summary (top 10 by update_count)")
            print("  " + "-" * 56)
            rows = sorted(
                summary["features"].items(),
                key=lambda kv: kv[1]["update_count"],
                reverse=True,
            )[:10]
            hdr = f"  {'feature':<30} {'updated':>8} {'skipped':>8} {'late':>8}"
            print(hdr)
            for name, counts in rows:
                print(
                    f"  {name:<30} "
                    f"{counts['update_count']:>8} "
                    f"{counts['skip_count']:>8} "
                    f"{counts['late_drop_count']:>8}"
                )
            print()


if __name__ == "__main__":
    main()
