"""
Benchmark Feature Store operations.

Measures latency of the hot path (get_latest, get_all_latest, get_window) and
throughput of the persistence path (append + flush), plus manifest query vs
rglob query speedup.

Usage
-----
    python scripts/benchmark_feature_store.py

Options
-------
    --n N           Iterations for latency benchmarks (default: 100_000)
    --events N      Events for append+flush benchmark (default: 10_000)
    --instruments N Number of distinct instruments (default: 3)
    --fsids N       Number of distinct feature sets (default: 2)

Does NOT require compiled Nautilus, ccxt, or any ML library.

Example output
--------------
    === OnlineFeatureStore latency (N=100000, 3 instruments, 2 feature sets) ===
    get_latest            :   0.12 µs / call
    get_all_latest        :   0.18 µs / call
    get_window(n=50)      :   1.43 µs / call

    === OfflineFeatureStore throughput (10000 events) ===
    append+flush          :  25.3 ms total,  2530.0 events/s

    === Manifest query vs rglob (50 files, query window = 10% of range) ===
    rglob query           :   2.14 ms
    manifest query        :   0.09 ms
    speedup               :  23.8 x
"""
from __future__ import annotations

import argparse
import tempfile
import time
from pathlib import Path

from nautilus_ext.features.feature_event import FeatureEvent
from nautilus_ext.features.feature_manifest import FeatureManifest, ManifestRecord
from nautilus_ext.features.feature_store import OfflineFeatureStore, OnlineFeatureStore

_TS0 = 1_704_067_200_000  # 2024-01-01 00:00:00 UTC ms


def _make_event(
    i: int,
    instrument_id: str = "BTC.BINANCE",
    feature_set_id: str = "bench_v1",
) -> FeatureEvent:
    return FeatureEvent(
        ts_event=_TS0 + i * 60_000,
        instrument_id=instrument_id,
        feature_set_id=feature_set_id,
        feature_version="1",
        values={"x": float(i), "y": float(i) * 1.5, "z": float(i) * 0.5},
    )


def _fmt(label: str, us: float, width: int = 22) -> str:
    return f"  {label:<{width}}: {us:>8.2f} µs / call"


def _fmt_ms(label: str, ms: float, extra: str = "", width: int = 22) -> str:
    return f"  {label:<{width}}: {ms:>8.1f} ms{extra}"


# ---------------------------------------------------------------------------
# Benchmark functions
# ---------------------------------------------------------------------------

def bench_online_latency(n: int, n_instruments: int, n_fsids: int) -> None:
    print(
        f"\n=== OnlineFeatureStore latency "
        f"(N={n:,}, {n_instruments} instruments, {n_fsids} feature sets) ==="
    )
    store = OnlineFeatureStore(window_size=200)
    instruments = [f"IID_{i}.VENUE" for i in range(n_instruments)]
    fsids = [f"fs_{j}" for j in range(n_fsids)]

    # Pre-populate
    for i in range(200):
        for iid in instruments:
            for fsid in fsids:
                store.put(_make_event(i, instrument_id=iid, feature_set_id=fsid))

    iid0, fsid0 = instruments[0], fsids[0]

    # get_latest
    t0 = time.perf_counter()
    for _ in range(n):
        store.get_latest(iid0, fsid0)
    us_latest = (time.perf_counter() - t0) / n * 1e6
    print(_fmt("get_latest", us_latest))

    # get_all_latest
    t0 = time.perf_counter()
    for _ in range(n):
        store.get_all_latest(iid0)
    us_all = (time.perf_counter() - t0) / n * 1e6
    print(_fmt("get_all_latest", us_all))

    # get_window(n=50)
    t0 = time.perf_counter()
    for _ in range(n):
        store.get_window(iid0, fsid0, n=50)
    us_window = (time.perf_counter() - t0) / n * 1e6
    print(_fmt("get_window(n=50)", us_window))


def bench_offline_throughput(n_events: int, tmp_dir: Path) -> None:
    print(f"\n=== OfflineFeatureStore throughput ({n_events:,} events) ===")
    store = OfflineFeatureStore(tmp_dir / "bench_offline", flush_threshold=n_events + 1)
    events = [_make_event(i) for i in range(n_events)]

    t0 = time.perf_counter()
    for e in events:
        store.append(e)
    store.flush()
    elapsed_ms = (time.perf_counter() - t0) * 1e3
    throughput = n_events / (elapsed_ms / 1e3)
    print(_fmt_ms("append+flush", elapsed_ms, f"  ({throughput:,.0f} events/s)"))


def bench_manifest_vs_rglob(n_files: int, tmp_dir: Path) -> None:
    print(f"\n=== Manifest query vs rglob ({n_files} files, query = 10% of range) ===")

    # Write n_files Parquet files spanning n_files * 1 day
    import pandas as pd

    offline_root = tmp_dir / "bench_rglob_offline"
    fsid, iid = "bench_v1", "BTC.BINANCE"
    safe_iid = iid.replace(".", "_")
    store_dir = offline_root / "offline" / fsid / safe_iid
    store_dir.mkdir(parents=True, exist_ok=True)

    manifest_path = offline_root / "feature_manifest.json"
    manifest = FeatureManifest(manifest_path)

    day_ms = 86_400_000
    for i in range(n_files):
        start = _TS0 + i * day_ms
        end = start + day_ms - 1
        p = store_dir / f"{start}-{end}.parquet"
        rows = [_make_event(j, instrument_id=iid, feature_set_id=fsid).to_row()
                for j in range(10)]
        df = pd.DataFrame(rows)
        df["ts_event"] = range(start, start + 10)
        df.to_parquet(p, index=False, engine="pyarrow")
        manifest.append_file_record(ManifestRecord(
            feature_set_id=fsid, feature_version="1", instrument_id=iid,
            start_ts=start, end_ts=end, row_count=10,
            file_path=str(p), created_at="",
        ))
    manifest.save()

    # Query covers the middle 10% of the total range
    q_start = _TS0 + int(n_files * 0.45) * day_ms
    q_end = _TS0 + int(n_files * 0.55) * day_ms

    # rglob benchmark
    import glob as _glob
    t0 = time.perf_counter()
    for _ in range(20):
        list((offline_root / "offline").rglob("*.parquet"))
    t_rglob = (time.perf_counter() - t0) / 20 * 1e3

    # manifest benchmark
    t0 = time.perf_counter()
    for _ in range(20):
        manifest.find_files(feature_set_id=fsid, instrument_id=iid, start=q_start, end=q_end)
    t_manifest = (time.perf_counter() - t0) / 20 * 1e3

    speedup = t_rglob / t_manifest if t_manifest > 0 else float("inf")
    print(_fmt_ms("rglob (all files)", t_rglob))
    print(_fmt_ms("manifest query", t_manifest))
    print(f"  {'speedup':<22}: {speedup:>8.1f} x")


def bench_manifest_maintenance(tmp_dir: Path) -> None:
    print("\n=== FeatureManifest maintenance operations ===")
    m = FeatureManifest(tmp_dir / "maint_manifest.json")

    # Insert 1000 records with 10% duplicates
    n = 1000
    for i in range(n):
        m.append_file_record(ManifestRecord(
            feature_set_id="fs_a", feature_version="1", instrument_id="BTC.BINANCE",
            start_ts=_TS0 + i * 60_000, end_ts=_TS0 + i * 60_000 + 59_999,
            row_count=60, file_path=f"/tmp/fake_{i}.parquet", created_at="",
        ))
    # Add 100 duplicates
    for i in range(100):
        m.append_file_record(ManifestRecord(
            feature_set_id="fs_a", feature_version="1", instrument_id="BTC.BINANCE",
            start_ts=_TS0 + i * 60_000, end_ts=_TS0 + i * 60_000 + 59_999,
            row_count=60, file_path=f"/tmp/fake_{i}.parquet", created_at="",
        ))
    print(f"  Before deduplicate: {len(m)} records")

    t0 = time.perf_counter()
    removed = m.deduplicate()
    t_dedup = (time.perf_counter() - t0) * 1e3
    print(f"  After  deduplicate: {len(m)} records (removed {removed}) — {t_dedup:.2f} ms")

    t0 = time.perf_counter()
    stats = m.summary()
    t_summary = (time.perf_counter() - t0) * 1e3
    total_rows = sum(
        v["total_row_count"]
        for fsid_stats in stats.values()
        for v in fsid_stats.values()
    )
    print(f"  summary()          : {total_rows} total rows across all groups — {t_summary:.2f} ms")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Benchmark Feature Store operations")
    parser.add_argument("--n", type=int, default=100_000, help="Iterations for latency benchmarks")
    parser.add_argument("--events", type=int, default=10_000, help="Events for flush benchmark")
    parser.add_argument("--instruments", type=int, default=3, help="Number of instruments")
    parser.add_argument("--fsids", type=int, default=2, help="Number of feature sets")
    parser.add_argument("--files", type=int, default=50, help="Number of Parquet files for rglob test")
    args = parser.parse_args()

    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)
        bench_online_latency(args.n, args.instruments, args.fsids)
        bench_offline_throughput(args.events, tmp_dir)
        bench_manifest_vs_rglob(args.files, tmp_dir)
        bench_manifest_maintenance(tmp_dir)


if __name__ == "__main__":
    main()
