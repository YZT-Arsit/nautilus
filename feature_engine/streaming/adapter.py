"""Streaming source adapters.

A source adapter is anything that yields ``pl.DataFrame`` micro-batches with
the canonical bar/tick schema. We define a minimal Protocol and ship two
implementations:

  * :class:`ReplayAdapter` – replays a historical Parquet partition at
    accelerated wall time. Critical for simulation and parity tests.
  * :class:`CallbackAdapter` – an inbox you push batches into from outside
    (e.g. a Nautilus message-bus handler, a ZMQ subscriber, a Kafka consumer).
"""
from __future__ import annotations

import queue
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Protocol

import polars as pl


class StreamSource(Protocol):
    """Anything that yields ``pl.DataFrame`` micro-batches."""

    def __iter__(self) -> Iterator[pl.DataFrame]: ...


class ReplayAdapter:
    """Replay a historical Parquet partition as a stream.

    Batches are grouped by ``batch_ms`` of event-time. Setting
    ``speed=float('inf')`` replays as fast as the consumer can handle — the
    canonical setup for offline parity tests.
    """

    def __init__(
        self,
        path: Path | str,
        *,
        batch_ms: int = 1000,
        speed: float = float("inf"),
        ts_col: str = "ts_event",
    ) -> None:
        self.path = Path(path)
        self.batch_ms = batch_ms
        self.speed = speed
        self.ts_col = ts_col

    def __iter__(self) -> Iterator[pl.DataFrame]:
        df = pl.read_parquet(self.path).sort(self.ts_col)
        if df.is_empty():
            return

        # Bucket rows by event-time slot of width ``batch_ms``.
        bucketed = df.with_columns(
            (pl.col(self.ts_col).dt.epoch("ms") // self.batch_ms).alias("_bucket")
        )
        prev_bucket: int | None = None
        for bucket_df in bucketed.partition_by("_bucket", maintain_order=True):
            this_bucket = bucket_df["_bucket"][0]
            if prev_bucket is not None and self.speed != float("inf"):
                dt_real = (this_bucket - prev_bucket) * self.batch_ms / 1000 / self.speed
                if dt_real > 0:
                    time.sleep(dt_real)
            prev_bucket = this_bucket
            yield bucket_df.drop("_bucket")


class CallbackAdapter:
    """Push-based source. External code calls :meth:`push`; iteration drains.

    Use when integrating with an event-driven runtime like Nautilus: the
    on-bar / on-quote callback shoves rows in here, the streaming engine pulls
    them out in its own loop or thread.
    """

    def __init__(self, maxsize: int = 1024) -> None:
        self._q: queue.Queue[pl.DataFrame | None] = queue.Queue(maxsize=maxsize)

    def push(self, batch: pl.DataFrame) -> None:
        """Enqueue a micro-batch. Blocks if the queue is full (back-pressure)."""
        self._q.put(batch)

    def close(self) -> None:
        """Signal end-of-stream so consumers exit their iteration cleanly."""
        self._q.put(None)

    def __iter__(self) -> Iterator[pl.DataFrame]:
        while True:
            item = self._q.get()
            if item is None:
                return
            yield item
