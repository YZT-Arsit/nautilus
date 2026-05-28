"""Example: simulate a streaming session by replaying a historical partition.

This is also the foundation for the batch≡streaming parity test:

  1. Pick a raw partition.
  2. Compute features over it offline (via :class:`BatchEngine` or directly).
  3. Replay the same partition through the streaming engine.
  4. Diff the two outputs — they should be equal up to floating-point tolerance.

Usage::

    python -m quant_feature_engine.examples.streaming_sim \
        --config quant_feature_engine/config/example.yaml \
        --raw-file data/raw/.../part-000.parquet \
        --batch-ms 500
"""
from __future__ import annotations

import argparse
import logging
from pathlib import Path

from quant_feature_engine.config.loader import load_config
from quant_feature_engine.core.dag import FeatureDAG
from quant_feature_engine.core.state import MemoryStateStore, RedisStateStore
from quant_feature_engine.features import load_all
from quant_feature_engine.streaming.adapter import ReplayAdapter
from quant_feature_engine.streaming.engine import StreamingEngine, StreamingEngineConfig


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    parser.add_argument("--raw-file", required=True)
    parser.add_argument("--batch-ms", type=int, default=1000)
    parser.add_argument("--feature-set", default="technical_v1")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    load_all()
    cfg = load_config(args.config)

    feature_names = next(fs.features for fs in cfg.feature_sets if fs.name == args.feature_set)
    dag = FeatureDAG(feature_names)

    state_store = (
        RedisStateStore(cfg.streaming.redis_url)
        if cfg.streaming.redis_url
        else MemoryStateStore()
    )

    engine = StreamingEngine(
        dag,
        state_store=state_store,
        config=StreamingEngineConfig(
            checkpoint_every_n_batches=cfg.streaming.checkpoint_every_n_batches,
        ),
    )

    source = ReplayAdapter(Path(args.raw_file), batch_ms=args.batch_ms)
    stats = engine.run(source)
    print(
        f"Streaming sim done: batches={stats.batches} rows={stats.rows} "
        f"checkpoints={stats.checkpoints} errors={stats.errors}"
    )


if __name__ == "__main__":
    main()
