#!/usr/bin/env python3
"""Read-only audit of the canonical 1m fields used by session VWAP."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--market-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    import pyarrow.dataset as ds

    partition = (
        args.market_root / "asset_class=crypto/exchange=BINANCE/venue_type=futures_um"
        / "symbol=BTCUSDT/data_type=bar/freq=1m"
    )
    files = sorted(partition.glob("date=*/part-*.parquet"))
    if not files:
        raise FileNotFoundError(f"no canonical BTCUSDT 1m partitions under {partition}")
    schema = ds.dataset(str(files[0]), format="parquet").schema
    names = set(schema.names)
    required = {"ts", "open", "high", "low", "close", "volume"}
    payload = {
        "status": "passed" if required <= names else "failed",
        "partition_root": str(partition), "partition_count": len(files),
        "first_partition": str(files[0]), "last_partition": str(files[-1]),
        "schema": [str(field) for field in schema],
        "required_fields_present": sorted(required.intersection(names)),
        "missing_required_fields": sorted(required - names),
        "quote_volume_present": "quote_volume" in names,
        "session_vwap_source": (
            "source_quote_volume" if "quote_volume" in names
            else "explicit_close_times_volume_fallback_with_counter"
        ),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_suffix(args.output.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, args.output)
    print(json.dumps(payload, ensure_ascii=False))
    return 0 if payload["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())

