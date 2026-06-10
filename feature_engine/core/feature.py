"""Feature abstract base class.

Design contract
---------------
A ``Feature`` is the **smallest unit of computation**. It processes a Polars
DataFrame (an Arrow-backed micro-batch) and emits one or more new columns. The
same code path runs in offline backfill and live streaming; the only difference
is the *size* and *cadence* of the micro-batches fed in.

A feature owns:
  * declarative metadata: name, inputs, outputs, dependencies, window, warmup,
    cross-day policy.
  * a single state object (kept in ``self._state``) that survives between
    ``update()`` calls in streaming, and is reset to a clean slate before
    backfill via ``reset()``.

Two API methods:
  * ``update(batch)``  – the universal incremental method.
  * ``compute_batch(df)`` – defaults to ``reset(); update(df)`` but may be
    overridden by a feature that has a meaningfully faster vectorised form.

The test suite asserts ``compute_batch(df)`` equals
``concat(update(chunk) for chunk in chunks(df))`` for every registered feature,
which is the contract that lets us trust batch≡streaming parity.
"""
from __future__ import annotations

import pickle
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Literal

import polars as pl


CrossDayPolicy = Literal["continuous", "reset"]
"""How rolling windows behave at trading-day boundaries.

  * ``"continuous"`` – state carries across days. Use this for true intraday
    momentum where overnight gaps are part of the signal.
  * ``"reset"``      – feature state is reset between trading dates. Use this
    for indicators that should not see prior-day prices (e.g. session VWAP,
    open-relative momentum).

The default is ``"continuous"``; switch per-feature in the FeatureMeta.
"""


@dataclass(frozen=True)
class FeatureMeta:
    """Declarative metadata for a feature.

    Attributes
    ----------
    name : Stable identifier; also the partition tag in ``manifest.parquet``.
    inputs : Column names this feature reads (raw or other features).
    outputs : Column names this feature writes. Usually ``[name]`` but multi-
        output features (e.g. MACD) emit several.
    dependencies : Names of other features that must compute first. If empty,
        the feature only consumes raw columns.
    window : Number of trailing rows required (per symbol). 0 means stateless.
    warmup : Number of leading rows after which output is considered valid
        (typically equal to ``window``). Earlier rows return null.
    feature_group : Logical bucket used for the on-disk partition.
    version : Bumped whenever the implementation changes in a non-compatible
        way. The manifest tracks (partition, name, version) so we never re-use
        stale outputs.
    params : Arbitrary hyper-parameters (window length, alpha, etc.). Stored in
        the manifest so reruns with different params don't collide.
    cross_day : Cross-day rolling policy; see :data:`CrossDayPolicy`.
    """

    name: str
    inputs: tuple[str, ...]
    outputs: tuple[str, ...]
    dependencies: tuple[str, ...] = ()
    window: int = 0
    warmup: int = 0
    feature_group: str = "technical"
    version: int = 1
    params: dict[str, Any] = field(default_factory=dict)
    cross_day: CrossDayPolicy = "continuous"


class Feature(ABC):
    """Abstract feature. Subclasses implement ``update`` and declare ``meta``."""

    meta: FeatureMeta

    def __init__(self) -> None:
        self._state: dict[str, Any] = {}
        self.reset()

    # ------------------------------------------------------------------ core

    @abstractmethod
    def update(self, batch: pl.DataFrame) -> pl.DataFrame:
        """Process one micro-batch and return *only the output columns*.

        Implementations must:
          * be deterministic given the same input + state,
          * preserve row order,
          * return a frame of the same length as ``batch`` (one row per input
            row), with ``self.meta.outputs`` as the columns. Use nulls for
            rows still inside the warm-up window.

        State mutation lives in ``self._state``. Per-symbol features should
        key state by ``symbol`` (see :class:`PerSymbolMixin`).
        """

    def compute_batch(self, df: pl.DataFrame) -> pl.DataFrame:
        """Process the entire dataframe. Default: reset then update once.

        Override only when a vectorised form is substantially faster than the
        chunked incremental form (rare in Polars).
        """
        self.reset()
        return self.update(df)

    # ------------------------------------------------------------------ state

    def reset(self) -> None:
        """Drop all state. Always called by ``compute_batch`` default."""
        self._state = {}

    def snapshot(self) -> bytes:
        """Serialise state for checkpoint. Default uses pickle.

        Override with msgpack / Arrow IPC if pickle is too slow or if state
        must be readable from another language.
        """
        return pickle.dumps(self._state, protocol=pickle.HIGHEST_PROTOCOL)

    def restore(self, blob: bytes) -> None:
        """Load state previously produced by :meth:`snapshot`."""
        self._state = pickle.loads(blob)

    # ------------------------------------------------------------------ utils

    def __repr__(self) -> str:  # pragma: no cover - cosmetic
        return f"<Feature {self.meta.name} v{self.meta.version}>"


_ROW_INDEX = "__qfe_row_idx__"


class PerSymbolMixin:
    """Helper for features that maintain independent state per symbol.

    State layout: ``self._state[symbol] = <whatever>``. Subclasses use
    :meth:`process_per_symbol` which transparently handles the row-order
    reshuffling: input rows can be interleaved across symbols and we still
    return one output frame in the exact input order.
    """

    _state: dict[str, Any]
    meta: FeatureMeta

    def process_per_symbol(
        self,
        batch: pl.DataFrame,
        fn,
    ) -> pl.DataFrame:
        """Run ``fn(symbol, sub_batch_with_row_idx) -> output_with_row_idx``.

        The callable receives a per-symbol sub-frame **with a ``__qfe_row_idx__``
        column** that the helper attaches. It must return a frame of the same
        height that preserves that column. We then concat all sub-frames and
        sort by ``__qfe_row_idx__`` so the final output aligns with the input.

        When ``self.meta.cross_day == "reset"``, this also clears state for a
        symbol when the symbol's first row in the batch belongs to a new
        ``trading_date`` (or, if that column isn't present, when ``ts_event``
        crosses to a new UTC date). This is the canonical place to enforce the
        cross-day boundary so individual features don't have to.
        """
        tagged = batch.with_row_index(_ROW_INDEX)
        pieces: list[pl.DataFrame] = []
        for sub in tagged.partition_by("symbol", maintain_order=True):
            sym = str(sub["symbol"][0])
            if self.meta.cross_day == "reset":
                self._maybe_reset_for_new_day(sym, sub)
            pieces.append(fn(sym, sub))
        merged = pl.concat(pieces, how="vertical").sort(_ROW_INDEX)
        return merged.drop(_ROW_INDEX)

    def _maybe_reset_for_new_day(self, sym: str, sub: pl.DataFrame) -> None:
        """Reset state for ``sym`` when its first row in the batch is a new day.

        The policy is deliberately conservative: we only inspect the *first*
        row of the sub-batch. A batch spanning two days will still see
        continuous state within the batch — that's a fundamental limitation
        of micro-batching and is the user's responsibility to avoid (chunk by
        day, not by row-count).
        """
        if sub.is_empty():
            return
        if "trading_date" in sub.columns:
            new_day = str(sub["trading_date"][0])
        else:
            ts = sub["ts_event"][0]
            new_day = ts.date().isoformat() if ts is not None else ""

        marker_attr = "_qfe_last_day"
        last_seen = self._state.setdefault(marker_attr, {})
        prev_day = last_seen.get(sym)
        if prev_day is not None and prev_day != new_day:
            # Drop just this symbol's state; other symbols are unaffected.
            self._state.pop(sym, None)
        last_seen[sym] = new_day
