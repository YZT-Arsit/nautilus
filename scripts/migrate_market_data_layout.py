#!/usr/bin/env python3
"""Re-lay-out ``market_data`` from the old Binance-Vision layout to the locked one.

OLD:  <root>/exchange=E/venue_type=V/symbol=S/bar_type=B/date=D/part-*.parquet
NEW:  <root>/asset_class=A/exchange=E/venue_type=V/symbol=S/data_type=bar/freq=B/date=D/part-*.parquet

Parquet bodies do NOT store the partition columns, so this is a pure directory
re-layout: each file is MOVED to its new path — no parquet rewrite. Stdlib only;
run on the server where the data lives::

    # preview (default):
    uv run python scripts/migrate_market_data_layout.py --root historical_data/market_data
    # apply:
    uv run python scripts/migrate_market_data_layout.py --root historical_data/market_data --apply

Idempotent: files already under an ``asset_class=``/``data_type=`` path are skipped.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path


def _kv(segment: str) -> tuple[str, str] | None:
    if "=" in segment:
        k, v = segment.split("=", 1)
        return k, v
    return None


def plan_moves(root: Path, asset_class: str) -> list[tuple[Path, Path]]:
    """Return (src, dest) pairs for every parquet under an old-layout partition."""
    moves: list[tuple[Path, Path]] = []
    for src in root.rglob("*.parquet"):
        parts = {kv[0]: kv[1] for seg in src.parts if (kv := _kv(seg))}
        if "asset_class" in parts:  # already migrated
            continue
        if not all(k in parts for k in ("exchange", "venue_type", "symbol", "date")):
            continue
        if "bar_type" in parts:  # old BAR layout
            data_type, freq = "bar", parts["bar_type"]
        elif "data_type" in parts:  # old TRADE/quote layout (aggTrades -> trade)
            data_type = "trade" if parts["data_type"] == "aggTrades" else parts["data_type"]
            freq = "tick"
        else:
            continue
        dest = (
            root
            / f"asset_class={asset_class}"
            / f"exchange={parts['exchange']}"
            / f"venue_type={parts['venue_type']}"
            / f"symbol={parts['symbol']}"
            / f"data_type={data_type}"
            / f"freq={freq}"
            / f"date={parts['date']}"
            / src.name
        )
        moves.append((src, dest))
    return moves


def prune_empty(root: Path) -> None:
    """Remove now-empty old-layout directories (deepest first)."""
    for d in sorted((p for p in root.rglob("*") if p.is_dir()),
                    key=lambda p: len(p.parts), reverse=True):
        segs = {kv[0] for seg in d.parts if (kv := _kv(seg))}
        if "asset_class" in segs:  # never touch new-layout dirs
            continue
        try:
            d.rmdir()  # only succeeds if empty
        except OSError:
            pass


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Migrate market_data to the locked layout")
    parser.add_argument("--root", default="historical_data/market_data")
    parser.add_argument("--asset-class", default="crypto")
    parser.add_argument("--apply", action="store_true", help="actually move (default: dry-run)")
    args = parser.parse_args(argv)

    root = Path(args.root)
    if not root.exists():
        parser.error(f"root not found: {root}")

    moves = plan_moves(root, args.asset_class)
    print(f"[migrate] {len(moves)} file(s) to relayout under {root}")
    for src, dest in moves[:5]:
        print(f"  {src}\n    -> {dest}")
    if len(moves) > 5:
        print(f"  ... and {len(moves) - 5} more")

    if not args.apply:
        print("[migrate] dry-run; pass --apply to move files")
        return

    moved = 0
    for src, dest in moves:
        dest.parent.mkdir(parents=True, exist_ok=True)
        os.replace(src, dest)  # atomic within the same filesystem
        moved += 1
    prune_empty(root)
    print(f"[migrate] moved {moved} file(s); pruned empty old dirs")


if __name__ == "__main__":
    main()
