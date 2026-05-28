"""Streaming feature engine.

Drives the feature DAG over a stream of micro-batches. State lives in each
feature instance for the lifetime of the engine and is periodically snapshotted
to a :class:`StateStore` so we can resume after a crash.

State isolation
---------------
Each checkpoint slot is addressed by a :class:`StateScope` that includes the
feature name, *feature version*, *params hash*, frequency, session id, and
(optionally) symbol. Bumping a feature's ``meta.version`` or changing a param
automatically lands the new state in a different slot — no manual cache busts,
no chance of replaying yesterday's EMA seed into today's recomputed alpha.

The loop is intentionally synchronous-by-default: micro-batch processing is
CPU-bound (Polars releases the GIL) and pulling in asyncio without need adds
complexity. If the source is async, wrap iteration in a thread or use the
:class:`CallbackAdapter` pattern.
"""
from __future__ import annotations

import logging
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import datetime, timezone

import polars as pl

from quant_feature_engine.core.dag import FeatureDAG
from quant_feature_engine.core.state import (
    MemoryStateStore,
    StateScope,
    StateStore,
    state_key,
)
from quant_feature_engine.storage.metadata import params_hash

logger = logging.getLogger(__name__)


@dataclass
class StreamingEngineConfig:
    """Tuning knobs for the streaming engine."""

    checkpoint_every_n_batches: int = 60
    """Snapshot all feature state every N batches. Trade-off: durability vs IO."""

    publish_partial: bool = True
    """If True, emit features even during warm-up (with nulls). Helps debugging."""

    session_id: str = ""
    """Session identifier (typically trading_date YYYY-MM-DD)."""

    frequency: str = "1m"
    """Bar frequency. Part of the state key — different frequencies never share state."""

    sort_input: bool = True
    """If True, sort every incoming micro-batch by (symbol, ts_event) before
    handing it to features. Disable only if you trust the source ordering."""

    def __post_init__(self) -> None:
        if not self.session_id:
            self.session_id = datetime.now(timezone.utc).strftime("%Y%m%d")


@dataclass
class StreamingStats:
    batches: int = 0
    rows: int = 0
    last_publish_ts: float = 0.0
    checkpoints: int = 0
    errors: int = 0
    output_buffer: list[pl.DataFrame] = field(default_factory=list)


class StreamingEngine:
    """Pull batches from a source, run features in DAG order, publish results.

    Parameters
    ----------
    dag : Pre-built :class:`FeatureDAG`.
    state_store : Where to checkpoint feature state. Defaults to in-memory.
    config : Tuning knobs.
    on_batch : Optional sink for emitted batches (e.g. write to message bus,
        push to downstream model server). If ``None`` we just buffer in
        :attr:`stats.output_buffer` for the EOD archiver to drain.
    """

    def __init__(
        self,
        dag: FeatureDAG,
        *,
        state_store: StateStore | None = None,
        config: StreamingEngineConfig | None = None,
        on_batch: Callable[[pl.DataFrame], None] | None = None,
    ) -> None:
        self.dag = dag
        self.state_store: StateStore = state_store or MemoryStateStore()
        self.config = config or StreamingEngineConfig()
        self.on_batch = on_batch
        self.features = dag.instantiate()
        self.stats = StreamingStats()
        self._restore()

    # ------------------------------------------------------------------ loop

    def run(self, source: Iterable[pl.DataFrame]) -> StreamingStats:
        """Block on the source until it's exhausted.

        Each iteration:
          1. Pull one micro-batch.
          2. Sort by (symbol, ts_event) if ``sort_input``.
          3. For each feature (in topo order) call ``update(batch)`` and
             ``hstack`` the result so downstream features see upstream outputs.
          4. Hand the enriched batch to ``on_batch`` (or buffer it).
          5. Maybe checkpoint.
        """
        for raw in source:
            try:
                enriched = self._process_one(raw)
            except Exception:  # noqa: BLE001 — top-level loop must keep running
                self.stats.errors += 1
                logger.exception("Streaming batch failed; continuing")
                continue

            self.stats.batches += 1
            self.stats.rows += enriched.height
            self.stats.last_publish_ts = time.time()

            if self.on_batch is not None:
                self.on_batch(enriched)
            else:
                self.stats.output_buffer.append(enriched)

            if self.stats.batches % self.config.checkpoint_every_n_batches == 0:
                self.checkpoint()

        # Final checkpoint on clean shutdown.
        self.checkpoint()
        return self.stats

    def _process_one(self, batch: pl.DataFrame) -> pl.DataFrame:
        if batch.is_empty():
            return batch
        if self.config.sort_input:
            batch = batch.sort(["symbol", "ts_event"])
        out = batch
        for name in self.dag.order:
            f = self.features[name]
            # Project to the feature's declared inputs only. This is both a
            # correctness guard (features cannot accidentally read columns
            # they did not subscribe to) and a schema-stability guarantee
            # (a feature's saved tail always has the same column set across
            # micro-batches, no matter what other features have run since).
            present = [c for c in f.meta.inputs if c in out.columns]
            proj = out.select(present) if present else out
            cols = f.update(proj)
            if cols.height != out.height:
                raise ValueError(
                    f"Feature {name} returned {cols.height} rows for "
                    f"{out.height}-row input"
                )
            out = out.hstack(cols)
        return out

    # ------------------------------------------------------------------ state

    def checkpoint(self) -> None:
        """Persist every feature's state to the configured store.

        We serialise the *whole* feature state dict in one blob keyed without a
        symbol field (``scope.symbol = None``). The dict itself is keyed by
        symbol, so we still get per-symbol isolation at runtime.
        """
        for name, f in self.features.items():
            scope = StateScope(
                feature_name=name,
                feature_version=f.meta.version,
                params_hash=params_hash(f.meta.params),
                frequency=self.config.frequency,
                session=self.config.session_id,
                symbol=None,
            )
            self.state_store.put(state_key(scope), f.snapshot())
        self.stats.checkpoints += 1
        logger.info("Checkpointed %d features", len(self.features))

    def _restore(self) -> None:
        """Restore each feature's state from the store, if present."""
        for name, f in self.features.items():
            scope = StateScope(
                feature_name=name,
                feature_version=f.meta.version,
                params_hash=params_hash(f.meta.params),
                frequency=self.config.frequency,
                session=self.config.session_id,
                symbol=None,
            )
            blob = self.state_store.get(state_key(scope))
            if blob is not None:
                f.restore(blob)
                logger.info("Restored state for feature %s", name)

    def drain(self) -> pl.DataFrame | None:
        """Concatenate the buffered output and clear the buffer."""
        if not self.stats.output_buffer:
            return None
        df = pl.concat(self.stats.output_buffer, how="vertical_relaxed")
        self.stats.output_buffer.clear()
        return df
