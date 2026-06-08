#!/usr/bin/env python3
"""
Lightweight benchmark for the SpecFeatureEngine incremental hot path.

Usage
-----
    python -m scripts.benchmark_feature_engine [options]

Examples
--------
    # default: 100 000 bar events, 20 features, window 100
    python -m scripts.benchmark_feature_engine

    # raw-only: 20 bar features, no derived
    python -m scripts.benchmark_feature_engine --event-kind bar --features 20

    # derived feature chains: spread/mid → ratio + log_return → rolling_std
    python -m scripts.benchmark_feature_engine --event-kind mixed --derived --features 10

    # stress: 100 features, large window
    python -m scripts.benchmark_feature_engine --events 100000 --features 100 --window 1000

    # mixed bar + quote events (features split by kind)
    python -m scripts.benchmark_feature_engine --event-kind mixed --events 100000 --features 20

    # with profiling
    python -m scripts.benchmark_feature_engine --derived --event-kind mixed --profile

Metrics reported
----------------
    total elapsed       total wall-clock time for all on_event() calls
    avg on_event        mean latency per call (µs)
    p50 / p95 / p99     percentile latencies (µs)
    events/sec          throughput
    feature·events/s    throughput × n_features (total incremental updates/sec)
    raw features        number of raw (market-event-subscribed) features
    derived features    number of derived (feature-to-feature) features

Interpretation
--------------
  - avg on_event scales linearly with --features (O(n_features) per event by design).
  - Increasing --window has NO effect on steady-state latency; the ring buffer is
    fixed-size (O(1) push regardless of window size).  If you see regression here,
    something on the hot path has grown unbounded.
  - p99 < 100 µs for 20 features on a quiet machine is a reasonable baseline;
    actual values depend heavily on CPU, Python version, and OS scheduler.
  - For --event-kind mixed: each event advances only features subscribed to that
    event type; throughput reflects O(n_subscribed_features + n_dirty_derived) per event.
  - --derived adds two practical chains on top of raw features:
      spread + mid_price → spread_ratio (ratio derived)
      log_return + [rolling_std_derived × --derived-chains] (realized-vol derived)

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
# Synthetic events
# ---------------------------------------------------------------------------

@dataclass
class _SyntheticBar:
    open: float = 100.0
    high: float = 101.0
    low: float = 99.0
    close: float = 100.5
    volume: float = 1000.0
    event_type: str = "bar"
    instrument_id: str = "BENCH/USDT"
    event_time_ns: int = 0
    receive_time_ns: int | None = None


@dataclass
class _SyntheticQuote:
    bid_price: float = 100.0
    ask_price: float = 100.1
    bid_size: float = 10.0
    ask_size: float = 10.0
    event_type: str = "quote"
    instrument_id: str = "BENCH/USDT"
    event_time_ns: int = 0
    receive_time_ns: int | None = None


# ---------------------------------------------------------------------------
# Spec / event factories
# ---------------------------------------------------------------------------

def _make_bar_specs(n_features: int, window: int):
    from nautilus_ext.features.compute.spec import FeatureSpec

    _ENTRIES: list[tuple[str, str | None]] = [
        ("rolling_mean",       "close"),
        ("rolling_std",        "close"),
        ("rolling_min",        "low"),
        ("rolling_max",        "high"),
        ("rolling_sum",        "volume"),
        ("rolling_volume_sum", None),
        ("ewma",               "close"),
        ("simple_return",      "close"),
        ("log_return",         "close"),
        ("vwap",               None),
    ]
    specs = []
    for i in range(n_features):
        ftype, ifield = _ENTRIES[i % len(_ENTRIES)]
        specs.append(FeatureSpec(
            name=f"f{i}_{ftype}",
            input_type="bar",
            input_field=ifield,
            window=window,
            params={"type": ftype},
        ))
    return specs


def _make_quote_specs(n_features: int):
    from nautilus_ext.features.compute.spec import FeatureSpec
    _ENTRIES = [("spread", None), ("mid_price", None)]
    specs = []
    for i in range(n_features):
        ftype, _ = _ENTRIES[i % len(_ENTRIES)]
        specs.append(FeatureSpec(
            name=f"q{i}_{ftype}",
            input_type="quote",
            params={"type": ftype},
        ))
    return specs


def _make_derived_specs(n_chains: int, rvol_window: int, base_bar_spec_names: list[str]):
    """Return derived feature specs for two practical chain types.

    Chain A (quote-based): spread + mid_price → spread_ratio (ratio derived).
    Chain B (bar-based):   log_return → rolling_std_derived (realized volatility).

    ``base_bar_spec_names`` must contain at least one log_return feature name to
    anchor chain B.  If none are present, chain B specs are skipped.

    Returns a flat list of FeatureSpec (all derived).
    """
    from nautilus_ext.features.compute.spec import FeatureSpec

    derived: list = []

    # Chain A: spread_ratio per chain index
    for i in range(n_chains):
        spread_name = f"q{i * 2}_spread" if n_chains > 0 else "q0_spread"
        mid_name    = f"q{i * 2 + 1}_mid_price" if n_chains > 0 else "q1_mid_price"
        # Only add if both anchors exist in base specs
        if spread_name in base_bar_spec_names and mid_name in base_bar_spec_names:
            derived.append(FeatureSpec(
                name=f"spread_ratio_{i}",
                input_type="derived",
                depends_on=(spread_name, mid_name),
                params={"type": "ratio"},
            ))

    # Chain B: rolling_std_derived over each log_return feature found
    log_ret_names = [n for n in base_bar_spec_names if "log_return" in n]
    for lr_name in log_ret_names[:n_chains]:
        derived.append(FeatureSpec(
            name=f"rvol_{lr_name}",
            input_type="derived",
            depends_on=(lr_name,),
            window=rvol_window,
            params={"type": "rolling_std_derived"},
        ))

    return derived


def _make_bar_events(n: int, start_ns: int = 1_000_000_000) -> list[_SyntheticBar]:
    events: list[_SyntheticBar] = []
    base = 100.0
    for i in range(n):
        price = base + (i % 20) * 0.05
        events.append(_SyntheticBar(
            open=price - 0.1, high=price + 0.5, low=price - 0.5, close=price,
            volume=float(500 + i % 1000),
            event_time_ns=start_ns + i * 1_000_000_000,
        ))
    return events


def _make_quote_events(n: int, start_ns: int = 1_000_000_000) -> list[_SyntheticQuote]:
    events: list[_SyntheticQuote] = []
    base = 100.0
    for i in range(n):
        mid = base + (i % 10) * 0.02
        events.append(_SyntheticQuote(
            bid_price=mid - 0.05, ask_price=mid + 0.05,
            bid_size=float(10 + i % 50), ask_size=float(10 + i % 50),
            event_time_ns=start_ns + i * 500_000_000,  # 0.5 s spacing
        ))
    return events


def _make_mixed_events(
    n: int, bar_ratio: float = 0.6, start_ns: int = 1_000_000_000,
) -> list:
    """Alternate bar and quote events. bar_ratio controls the fraction of bars."""
    n_bar   = int(n * bar_ratio)
    n_quote = n - n_bar
    bars   = _make_bar_events(n_bar, start_ns=start_ns)
    quotes = _make_quote_events(n_quote, start_ns=start_ns + 250_000_000)
    # Interleave by timestamp
    merged = sorted(bars + quotes, key=lambda e: e.event_time_ns)
    return merged


# ---------------------------------------------------------------------------
# Stats helpers
# ---------------------------------------------------------------------------

def _pct(sorted_ns: list[int], p: float) -> float:
    """Return the p-th percentile of a pre-sorted list of ns values, in µs."""
    idx = min(int(len(sorted_ns) * p), len(sorted_ns) - 1)
    return sorted_ns[idx] / 1_000.0


def _fmt(label: str, value: str) -> str:
    return f"  {label:<28}{value:>14}"


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Benchmark SpecFeatureEngine incremental hot path",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--events",     type=int, default=100_000,
                        help="number of live events to process (default: 100 000)")
    parser.add_argument("--features",   type=int, default=20,
                        help="number of features in the engine (default: 20)")
    parser.add_argument("--window",     type=int, default=100,
                        help="rolling-window lookback size (default: 100)")
    parser.add_argument("--warmup",     type=int, default=0,
                        help="warmup events before benchmarking (default: same as --window)")
    parser.add_argument("--event-kind", choices=["bar", "quote", "mixed"], default="bar",
                        help="event stream kind: bar | quote | mixed (default: bar)")
    parser.add_argument("--derived",    action="store_true",
                        help="add derived feature chains on top of raw features "
                             "(spread→ratio, log_return→rolling_std_derived)")
    parser.add_argument("--derived-chains", type=int, default=2,
                        help="number of derived chains to add when --derived is set (default: 2)")
    parser.add_argument("--profile",    action="store_true",
                        help="enable engine profiling and print summary")
    args = parser.parse_args()

    n_events      = args.events
    n_feats       = args.features
    window        = args.window
    n_warmup      = args.warmup if args.warmup > 0 else window
    event_kind    = args.event_kind
    add_derived   = args.derived
    n_der_chains  = args.derived_chains

    from nautilus_ext.features.compute.engine import SpecFeatureEngine

    print()
    print("  SpecFeatureEngine Benchmark")
    print("  " + "-" * 40)
    print(_fmt("events:",      f"{n_events:,}"))
    print(_fmt("event kind:",  event_kind))
    print(_fmt("features:",    f"{n_feats}"))
    print(_fmt("window:",      f"{window}"))
    print(_fmt("warmup:",      f"{n_warmup}"))
    if add_derived:
        print(_fmt("derived chains:", f"{n_der_chains}"))

    # --- build specs ---
    t0 = time.perf_counter_ns()
    if event_kind == "bar":
        raw_specs     = _make_bar_specs(n_feats, window)
        n_bar_specs   = len(raw_specs)
        n_quote_specs = 0
    elif event_kind == "quote":
        raw_specs     = _make_quote_specs(n_feats)
        n_bar_specs   = 0
        n_quote_specs = len(raw_specs)
    else:  # mixed
        half          = max(1, n_feats // 2)
        bar_specs     = _make_bar_specs(half, window)
        quote_specs   = _make_quote_specs(n_feats - half)
        raw_specs     = bar_specs + quote_specs
        n_bar_specs   = len(bar_specs)
        n_quote_specs = len(quote_specs)

    derived_specs: list = []
    if add_derived:
        all_raw_names = [s.name for s in raw_specs]
        derived_specs = _make_derived_specs(n_der_chains, window, all_raw_names)

    specs = raw_specs + derived_specs
    n_raw_feats     = len(raw_specs)
    n_derived_feats = len(derived_specs)

    engine = SpecFeatureEngine(specs, stamp_process_time=False, profile=args.profile)
    build_ms = (time.perf_counter_ns() - t0) / 1e6
    print(_fmt("build time:",       f"{build_ms:.1f} ms"))
    print(_fmt("raw features:",     f"{n_raw_feats}"))
    print(_fmt("derived features:", f"{n_derived_feats}"))
    if event_kind == "mixed":
        print(_fmt("  bar features:",   f"{n_bar_specs}"))
        print(_fmt("  quote features:", f"{n_quote_specs}"))

    # --- warmup events ---
    if event_kind == "bar":
        warmup_events = _make_bar_events(n_warmup, start_ns=0)
    elif event_kind == "quote":
        warmup_events = _make_quote_events(n_warmup, start_ns=0)
    else:
        warmup_events = _make_mixed_events(n_warmup, start_ns=0)
    engine.warmup(warmup_events)

    # --- live events ---
    offset_ns = n_warmup * 1_000_000_000
    if event_kind == "bar":
        live_events = _make_bar_events(n_events, start_ns=offset_ns)
    elif event_kind == "quote":
        live_events = _make_quote_events(n_events, start_ns=offset_ns)
    else:
        live_events = _make_mixed_events(n_events, start_ns=offset_ns)

    # --- hot-path benchmark ---
    lats: list[int] = []
    t_start = time.perf_counter_ns()
    for ev in live_events:
        t_ev = time.perf_counter_ns()
        engine.on_event(ev)
        lats.append(time.perf_counter_ns() - t_ev)
    t_total = time.perf_counter_ns() - t_start

    actual_n = len(live_events)  # may differ from n_events for mixed (rounding)
    total_s   = t_total / 1e9
    avg_us    = (t_total / actual_n) / 1e3
    ups       = actual_n / total_s
    feat_eps  = ups * len(specs)

    lats.sort()
    p50 = _pct(lats, 0.50)
    p95 = _pct(lats, 0.95)
    p99 = _pct(lats, 0.99)

    print()
    print("  Results")
    print("  " + "-" * 40)
    print(_fmt("total elapsed:",    f"{total_s * 1000:.1f} ms"))
    print(_fmt("actual events:",    f"{actual_n:,}"))
    print(_fmt("avg on_event:",     f"{avg_us:.2f} µs"))
    print(_fmt("p50 on_event:",     f"{p50:.2f} µs"))
    print(_fmt("p95 on_event:",     f"{p95:.2f} µs"))
    print(_fmt("p99 on_event:",     f"{p99:.2f} µs"))
    print(_fmt("events/sec:",       f"{ups:,.0f}"))
    print(_fmt("feature·ev/sec:",   f"{feat_eps:,.0f}"))
    print()

    if args.profile:
        summary = engine.profile_summary()
        if summary.get("profile"):
            print("  Profile summary (top 10 by update_count)")
            print("  " + "-" * 64)
            rows = sorted(
                summary["features"].items(),
                key=lambda kv: kv[1]["update_count"],
                reverse=True,
            )[:10]
            hdr = (f"  {'feature':<32} {'updated':>8} "
                   f"{'skipped':>8} {'late':>8} {'last_status':>16}")
            print(hdr)
            for name, counts in rows:
                last = counts.get("last_status") or "-"
                print(
                    f"  {name:<32} "
                    f"{counts['update_count']:>8} "
                    f"{counts['skip_count']:>8} "
                    f"{counts['late_drop_count']:>8} "
                    f"{last:>16}"
                )
            print()


if __name__ == "__main__":
    main()
