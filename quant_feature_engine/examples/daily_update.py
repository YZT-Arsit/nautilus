"""Example: daily post-close update.

The same as a full backfill but scoped to one trading_date. Idempotent: the
manifest dedup makes re-running this a no-op.

Usage::

    python -m quant_feature_engine.examples.daily_update \
        --config quant_feature_engine/config/example.yaml \
        --trading-date 2026-05-26
"""
from __future__ import annotations

import argparse
import logging

from quant_feature_engine.config.loader import load_config
from quant_feature_engine.execution.batch_engine import BatchEngine
from quant_feature_engine.features import load_all
from quant_feature_engine.storage.metadata import Manifest


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    parser.add_argument("--trading-date", required=True)
    parser.add_argument("--asset-class", default="stock")
    parser.add_argument("--exchange", default="SSE")
    parser.add_argument("--frequency", default="1m")
    parser.add_argument("--feature-set", default="technical_v1")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO)
    load_all()
    cfg = load_config(args.config)
    feature_names = next(fs.features for fs in cfg.feature_sets if fs.name == args.feature_set)

    engine = BatchEngine(
        raw_root=cfg.storage.raw_root,
        feature_root=cfg.storage.feature_root,
        manifest=Manifest(cfg.storage.manifest_root),
        n_workers=cfg.execution.n_workers,
    )
    partitions = [
        {
            "asset_class": args.asset_class,
            "exchange": args.exchange,
            "frequency": args.frequency,
            "trading_date": args.trading_date,
        }
    ]
    results = engine.run(partitions, feature_names)
    print(f"Daily update: {results}")


if __name__ == "__main__":
    main()
