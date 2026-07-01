"""Offline feature computation: historical bars -> features -> ``feature_data``.

The single source of truth for feature maths is the operator library
(``feature_engine.compute.feature_lib``), driven by ``FeatureSpec``. This builder
runs the **same** ``SpecFeatureEngine`` over historical bars that the live/backtest
path uses, so offline-computed features are identical to streaming ones
(backtest ≡ offline parity by construction — one code path, not two).

Computed features are written to ``feature_data`` (a peer of ``market_data`` in
``historical_data/``) so they can be re-used by later runs as data — read back,
fed as inputs, recomputed and updated.

polars / pyarrow are imported lazily: importing this module is cheap; only the
``build_*`` / ``write_*`` paths need them. No Nautilus dependency.
"""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Iterable, Literal

if TYPE_CHECKING:  # pragma: no cover
    import polars as pl

    from data_engine.events import BarEvent
    from feature_engine.api import FeatureSpec

# Skeleton (non-feature) columns carried alongside feature values.
_KEY_COLS = ("symbol", "instrument_id", "ts_event")


class HistoricalFeatureBuilder:
    """Compute a set of ``FeatureSpec`` features over history and persist them.

    Parameters
    ----------
    specs
        The features to compute — the exact same specs a strategy declares in
        ``build_specs``. Reusing them guarantees offline == live values.
    feature_group
        Logical group the outputs belong to (the top ``feature_data`` partition,
        e.g. ``"technical"`` / ``"order_flow"``).
    """

    def __init__(self, specs: list["FeatureSpec"], *, feature_group: str = "technical") -> None:
        if not specs:
            raise ValueError("specs must not be empty")
        self.specs = list(specs)
        self.feature_group = feature_group

    # ---------------------------------------------------------------- compute

    def build_from_events(self, events: Iterable["BarEvent"]) -> "pl.DataFrame":
        """Run the spec engine over ``events``; one output row per event.

        A feature that is not yet ready emits ``None`` (point-in-time safe — no
        forward fill). Downstream training/eval drops warmup rows as needed.
        """
        import polars as pl  # noqa: PLC0415

        from feature_engine.compute import SpecFeatureEngine  # noqa: PLC0415

        engine = SpecFeatureEngine(specs=self.specs, stamp_process_time=False)
        feature_names = [s.name for s in self.specs]
        rows: list[dict] = []
        for ev in events:
            snap = engine.on_event(ev)
            row: dict = {
                "symbol": snap.instrument_id,
                "instrument_id": snap.instrument_id,
                "ts_event": snap.ts_event,
            }
            for name in feature_names:
                fv = snap.values.get(name)
                row[name] = fv.value if fv is not None else None
            rows.append(row)
        return pl.DataFrame(rows)

    def build_from_market_store(
        self,
        market_root: str | Path,
        *,
        instrument_id: str,
        frequency: str,
        trading_date: str | list[str],
        asset_class: str | None = None,
        exchange: str | None = None,
    ) -> "pl.DataFrame":
        """Read bars from a ``market_data`` dataset and compute features."""
        from feature_engine.storage.market_reader import MarketDataReader  # noqa: PLC0415

        bars = MarketDataReader(market_root).read_bars(
            asset_class=asset_class,
            exchange=exchange,
            frequency=frequency,
            trading_date=trading_date,
            instrument_id=instrument_id,
        )
        return self.build_from_events(bars)

    # ------------------------------------------------------------------ write

    def write_feature_data(
        self,
        df: "pl.DataFrame",
        *,
        feature_root: str | Path,
        asset_class: str,
        exchange: str,
        frequency: str,
        trading_date: str,
        instrument_id: str,
        manifest_root: str | Path | None = None,
        mode: Literal["error", "append", "overwrite"] = "overwrite",
    ) -> list[Path]:
        """Write features to ``feature_data`` (Hive parquet) + optional manifest.

        Layout is ``feature_engine.storage.layout.FEATURE_DATA_PARTITION_COLS`` —
        a peer of ``market_data`` (see ``docs/PLATFORM_ARCHITECTURE.md``).
        """
        from feature_engine.storage.layout import (  # noqa: PLC0415
            FEATURE_DATA_PARTITION_COLS,
            feature_data_path,
        )

        if mode not in {"error", "append", "overwrite"}:
            raise ValueError("mode must be one of error / append / overwrite")

        partition_values = {
            "feature_group": self.feature_group,
            "asset_class": asset_class,
            "exchange": exchange,
            "frequency": frequency,
            "trading_date": trading_date,
            "instrument_id": instrument_id,
        }
        target_dir = feature_data_path(feature_root, **partition_values)
        _prepare_partition(target_dir, mode)

        store = _feature_store(feature_root, FEATURE_DATA_PARTITION_COLS)
        written = store.write(df, partition_values=partition_values)

        if manifest_root is not None:
            self._append_manifest(
                manifest_root,
                partition_values=partition_values,
                feature_names=[s.name for s in self.specs],
                row_count=df.height,
            )
        return written

    # -------------------------------------------------------------- internal

    @staticmethod
    def _append_manifest(manifest_root, *, partition_values, feature_names, row_count) -> None:
        from feature_engine.storage.layout import PartitionKey  # noqa: PLC0415
        from feature_engine.storage.metadata import Manifest, params_hash  # noqa: PLC0415

        manifest = Manifest(Path(manifest_root) / "feature_manifest")
        key = PartitionKey.from_dict(
            partition_values,
            (
                "feature_group", "asset_class", "exchange",
                "frequency", "trading_date", "instrument_id",
            ),
        ).to_str()
        manifest.append(
            [
                {
                    "partition_key": key,
                    "feature_name": name,
                    "version": 1,
                    "params_hash": params_hash({"feature": name}),
                    "row_count": int(row_count),
                    "source": "offline-builder",
                }
                for name in feature_names
            ],
        )


def _feature_store(root, partition_cols):
    from feature_engine.storage.parquet_store import ParquetStore  # noqa: PLC0415

    return ParquetStore(root, partition_cols)


def _prepare_partition(path: Path, mode: str) -> None:
    if mode == "error" and path.exists() and any(path.glob("*.parquet")):
        raise FileExistsError(f"Partition {path} is non-empty and mode='error'")
    if mode == "overwrite" and path.exists():
        for part in path.glob("*.parquet"):
            part.unlink()


__all__ = ["HistoricalFeatureBuilder"]
