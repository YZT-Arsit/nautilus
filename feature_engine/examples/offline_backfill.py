"""Example: offline historical backfill across many days.

Usage::

    python -m quant_feature_engine.examples.offline_backfill \
        --config quant_feature_engine/config/example.yaml \
        --start 2026-01-02 --end 2026-05-26 \
        --asset-class stock --exchange SSE --frequency 1m
"""
from __future__ import annotations

import argparse
import logging
from datetime import date, timedelta
from pathlib import Path

from feature_engine.config.loader import load_config
from feature_engine.execution.batch_engine import BatchEngine
from feature_engine.features import load_all
from feature_engine.storage.metadata import Manifest


def _daterange(start: date, end: date):
    d = start
    while d <= end:
        yield d
        d += timedelta(days=1)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    parser.add_argument("--start", required=True, help="YYYY-MM-DD")
    parser.add_argument("--end", required=True, help="YYYY-MM-DD")
    parser.add_argument("--asset-class", default="stock")
    parser.add_argument("--exchange", default="SSE")
    parser.add_argument("--frequency", default="1m")
    parser.add_argument("--feature-set", default="technical_v1")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    load_all()
    cfg = load_config(args.config)

    feature_names = next(fs.features for fs in cfg.feature_sets if fs.name == args.feature_set)

    manifest = Manifest(cfg.storage.manifest_root)
    engine = BatchEngine(
        raw_root=cfg.storage.raw_root,
        feature_root=cfg.storage.feature_root,
        manifest=manifest,
        n_workers=cfg.execution.n_workers,
    )

    start = date.fromisoformat(args.start)
    end = date.fromisoformat(args.end)
    partitions = [
        {
            "asset_class": args.asset_class,
            "exchange": args.exchange,
            "frequency": args.frequency,
            "trading_date": d.isoformat(),
        }
        for d in _daterange(start, end)
    ]

    results = engine.run(partitions, feature_names, force=args.force)
    total = sum(r.get("rows", 0) for r in results)
    print(f"Backfill complete: {len(results)} partitions, {total:,} rows")


if __name__ == "__main__":
    main()
