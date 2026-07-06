#!/usr/bin/env python3
"""READ-ONLY inspection of the ``historical_data/market_data`` parquet store.

Walks the locked Hive layout
``asset_class/exchange/venue_type/symbol/data_type/freq/date`` and reports, per
partition, real row counts / timestamp ranges / monotonicity / duplicates /
schema. Downloads nothing, writes nothing except the two inventory artifacts:

* ``outputs/architecture_inventory/data_storage_inventory.csv``
* ``outputs/architecture_inventory/data_schema_samples.json``

Group-level summary rows (``date=ALL``) are emitted for every
(exchange, venue_type, symbol, freq) group using parquet *metadata only* (cheap).
Per-date detail rows (row counts, ts min/max, monotonic, duplicate) are emitted
for a configurable focus subset, reading only the timestamp column.
"""
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

# theoretical bars/day per freq (24h markets, e.g. crypto perpetual)
_EXPECTED_PER_DAY = {"1m": 1440, "3m": 480, "5m": 288, "15m": 96, "30m": 48,
                     "1h": 24, "2h": 12, "4h": 6, "1d": 1}
_TS_CANDIDATES = ("ts", "event_time_ns", "open_time", "timestamp")


def _read_meta(path: Path):
    import pyarrow.parquet as pq
    md = pq.read_metadata(str(path))
    return int(md.num_rows), [md.schema.names[i] for i in range(len(md.schema.names))]


def _ts_column(names: list[str]) -> str | None:
    for c in _TS_CANDIDATES:
        if c in names:
            return c
    return None


def _read_ts(path: Path, ts_col: str) -> list:
    import pyarrow.parquet as pq
    tbl = pq.read_table(str(path), columns=[ts_col])
    return tbl.column(0).to_pylist()


def _partitions(root: Path):
    """Yield dicts of Hive key=value plus the partition directory."""
    for date_dir in root.rglob("date=*"):
        if not date_dir.is_dir():
            continue
        parts = {}
        for seg in date_dir.parts:
            k, sep, v = seg.partition("=")
            if sep:
                parts[k] = v
        parts["_dir"] = date_dir
        yield parts


def _detail_row(parts: dict) -> dict:
    d = parts["_dir"]
    files = sorted(Path(d).glob("*.parquet"))
    freq = parts.get("freq", "")
    row = {
        "exchange": parts.get("exchange", ""), "venue_type": parts.get("venue_type", ""),
        "symbol": parts.get("symbol", ""), "bar_type": freq, "date": parts.get("date", ""),
        "file_path": str(files[0]) if files else str(d),
        "row_count": 0, "ts_min": "", "ts_max": "", "expected_rows": _EXPECTED_PER_DAY.get(freq, ""),
        "missing_rows": "", "duplicate_ts_count": "", "monotonic_ts": "",
        "schema": "", "status": "", "notes": "",
    }
    if not files:
        row["status"] = "empty"; row["notes"] = "no parquet in partition"; return row
    total = 0
    names: list[str] = []
    ts_all: list = []
    ts_col = None
    for f in files:
        n, nm = _read_meta(f)
        total += n
        names = names or nm
        ts_col = ts_col or _ts_column(nm)
        if ts_col:
            ts_all.extend(_read_ts(f, ts_col))
    row["row_count"] = total
    row["schema"] = ",".join(names)
    if ts_col and ts_all:
        row["ts_min"], row["ts_max"] = str(min(ts_all)), str(max(ts_all))
        row["monotonic_ts"] = all(ts_all[i] <= ts_all[i + 1] for i in range(len(ts_all) - 1))
        row["duplicate_ts_count"] = len(ts_all) - len(set(ts_all))
    exp = _EXPECTED_PER_DAY.get(freq)
    if exp:
        row["missing_rows"] = max(0, exp - total)
    ohlcv_ok = all(c in names for c in ("open", "high", "low", "close", "volume"))
    if not ohlcv_ok:
        row["status"] = "missing_ohlcv"
    elif ts_col is None:
        row["status"] = "no_timestamp"
    elif row["duplicate_ts_count"]:
        row["status"] = "duplicate_ts"
    elif row["monotonic_ts"] is False:
        row["status"] = "non_monotonic"
    elif exp and total != exp:
        row["status"] = "row_count_off"
    else:
        row["status"] = "ok"
    return row


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default="historical_data/market_data")
    ap.add_argument("--out", default="outputs/architecture_inventory")
    ap.add_argument("--detail-venue", default="futures_um")
    ap.add_argument("--detail-symbols", default="BTCUSDT,ETHUSDT,SOLUSDT,BNBUSDT")
    ap.add_argument("--detail-freqs", default="1m,15m")
    args = ap.parse_args()

    root = Path(args.root)
    out = Path(args.out); out.mkdir(parents=True, exist_ok=True)
    detail_syms = set(args.detail_symbols.split(","))
    detail_freqs = set(args.detail_freqs.split(","))

    all_parts = list(_partitions(root))
    print(f"found {len(all_parts)} date partitions under {root}")

    detail_rows: list[dict] = []
    groups: dict[tuple, list[dict]] = {}
    samples: dict[str, dict] = {}

    for parts in all_parts:
        gkey = (parts.get("exchange", ""), parts.get("venue_type", ""),
                parts.get("symbol", ""), parts.get("freq", ""))
        in_detail = (parts.get("venue_type") == args.detail_venue
                     and parts.get("symbol") in detail_syms
                     and parts.get("freq") in detail_freqs)
        if in_detail:
            r = _detail_row(parts)
            detail_rows.append(r)
            groups.setdefault(gkey, []).append(r)
        else:
            groups.setdefault(gkey, []).append({"date": parts.get("date", ""),
                                                "_dir": parts["_dir"]})
        # one schema sample per group
        skey = "/".join(gkey)
        if skey not in samples:
            files = sorted(Path(parts["_dir"]).glob("*.parquet"))
            if files:
                import pyarrow.parquet as pq
                pf = pq.ParquetFile(str(files[0]))
                sch = pf.schema_arrow
                head = pf.read_row_group(0).slice(0, 1).to_pylist()
                samples[skey] = {
                    "example_path": str(files[0]),
                    "row_count_file": pf.metadata.num_rows,
                    "schema": {sch.names[i]: str(sch.types[i]) for i in range(len(sch.names))},
                    "first_row": head[0] if head else {},
                }

    # group summary rows (date=ALL)
    summary_rows: list[dict] = []
    for gkey, items in sorted(groups.items()):
        ex, vt, sym, freq = gkey
        dates = sorted(str(i["date"]) for i in items if i.get("date"))
        detailed = [i for i in items if "row_count" in i]
        total_rows = sum(i.get("row_count", 0) for i in detailed) if detailed else ""
        statuses = sorted({i.get("status", "") for i in detailed if i.get("status")})
        summary_rows.append({
            "exchange": ex, "venue_type": vt, "symbol": sym, "bar_type": freq, "date": "ALL",
            "file_path": "", "row_count": total_rows,
            "ts_min": dates[0] if dates else "", "ts_max": dates[-1] if dates else "",
            "expected_rows": "", "missing_rows": "",
            "duplicate_ts_count": "", "monotonic_ts": "",
            "schema": ",".join(samples.get("/".join(gkey), {}).get("schema", {}).keys()),
            "status": f"{len(dates)} dates" + (f"; detail_status={','.join(statuses)}" if statuses else "; summary_only"),
            "notes": "detail rows present" if detailed else "group summary only (metadata)",
        })

    cols = ["exchange", "venue_type", "symbol", "bar_type", "date", "file_path",
            "row_count", "ts_min", "ts_max", "expected_rows", "missing_rows",
            "duplicate_ts_count", "monotonic_ts", "schema", "status", "notes"]
    with (out / "data_storage_inventory.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        w.writerows(summary_rows + sorted(detail_rows, key=lambda r: (r["symbol"], r["bar_type"], r["date"])))

    (out / "data_schema_samples.json").write_text(json.dumps(samples, indent=2, default=str),
                                                  encoding="utf-8")
    print(f"groups: {len(groups)}, detail rows: {len(detail_rows)}, samples: {len(samples)}")
    print(f"wrote {out/'data_storage_inventory.csv'} and data_schema_samples.json")


if __name__ == "__main__":
    main()
