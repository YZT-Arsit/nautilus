"""历史特征构建服务：market_data / DataFrame / events -> 特征 -> feature_data。

复用 feature_engine 既有、已被 parity 测试覆盖的原语：``FeatureDAG`` 解析依赖与
拓扑序，``Feature.update(batch)`` 逐特征计算（与 ``StreamingEngine._process_one``
同一算法，保证离线/流式一致）。本服务只做编排，不重写计算内核。

polars / pyarrow / feature_engine.core **懒加载**：``import`` 本服务不需要它们，
只有真正 ``build_*`` / ``write_*`` 时才导入，缺失时给出清晰错误。
不依赖 Nautilus。
"""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any, Iterable, Literal
from uuid import uuid4

if TYPE_CHECKING:  # pragma: no cover
    import polars as pl

    from data_engine.events import BarEvent

# feature_engine 侧排序键。
_SORT_KEYS = ("symbol", "ts_event")


class HistoricalFeatureBuilder:
    """把行情批量计算成历史特征并（可选）落入 feature_data。"""

    def __init__(self, features: list[str]) -> None:
        if not features:
            raise ValueError("features 不能为空")
        self.features = list(features)

    # ---------------------------------------------------------------- compute

    def build_from_dataframe(self, df: "pl.DataFrame") -> "pl.DataFrame":
        """在一个 Polars ``DataFrame`` 上计算所有请求特征，返回带特征列的表。

        算法等价于 ``StreamingEngine._process_one``：按 DAG 拓扑序，逐特征把输入
        列投影后调用 ``update``，再 ``hstack`` 输出列。
        """
        from feature_engine.core import registry as _registry  # noqa: PLC0415,F401
        from feature_engine.core.dag import FeatureDAG  # noqa: PLC0415

        dag = FeatureDAG(self.features)
        instances = dag.instantiate()

        out = df
        if all(k in out.columns for k in _SORT_KEYS):
            out = out.sort(list(_SORT_KEYS))
        for name in dag.order:
            f = instances[name]
            present = [c for c in f.meta.inputs if c in out.columns]
            proj = out.select(present) if present else out
            cols = f.update(proj)
            if cols.height != out.height:
                raise ValueError(
                    f"Feature {name} 返回 {cols.height} 行，期望 {out.height} 行"
                )
            out = out.hstack(cols)
        return out

    def build_from_events(self, events: Iterable["BarEvent"]) -> "pl.DataFrame":
        """从 ``BarEvent`` 序列计算特征（先经 ``bars_to_polars``）。"""
        from data_engine.adapters.dataframe_adapter import bars_to_polars  # noqa: PLC0415

        return self.build_from_dataframe(bars_to_polars(events))

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
        """从 market_data Hive 数据集读取行情并计算特征。"""
        from feature_engine.storage.market_reader import MarketDataReader  # noqa: PLC0415

        df = MarketDataReader(market_root).scan(
            asset_class=asset_class,
            exchange=exchange,
            frequency=frequency,
            trading_date=trading_date,
            instrument_id=instrument_id,
        )
        return self.build_from_dataframe(df)

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
        legacy_layout: bool = False,
    ) -> list[Path]:
        """把特征结果按 feature_group 落成 feature_data Hive Parquet。

        每个特征按其 ``meta.feature_group`` 归到对应分区；同一次调用可能写出多个
        feature_group 分区。默认写新版平级布局；``legacy_layout=True`` 时显式写
        旧 ``feature_group/frequency/trading_date`` 布局。可选地向
        ``manifest_root`` 追加 feature_manifest。
        """
        from feature_engine.core import registry as _registry  # noqa: PLC0415
        from feature_engine.storage.layout import (  # noqa: PLC0415
            FEATURE_DATA_PARTITION_COLS,
            LEGACY_FEATURE_PARTITION_COLS,
            feature_data_path,
        )

        try:
            import pyarrow as pa  # noqa: PLC0415
            import pyarrow.dataset as ds  # noqa: PLC0415
        except ImportError as exc:  # pragma: no cover
            raise ImportError("write_feature_data 需要 pyarrow。") from exc

        if mode not in {"error", "append", "overwrite"}:
            raise ValueError("mode 必须是 error / append / overwrite")

        partition_cols = (
            LEGACY_FEATURE_PARTITION_COLS
            if legacy_layout
            else FEATURE_DATA_PARTITION_COLS
        )

        # 按 feature_group 分组输出列。
        key_cols = [c for c in ("symbol", "instrument_id", "ts_event") if c in df.columns]
        group_to_cols: dict[str, list[str]] = {}
        for name in self.features:
            meta = _registry.get(name).meta
            for col in meta.outputs:
                if col in df.columns:
                    group_to_cols.setdefault(meta.feature_group, []).append(col)

        written: list[Path] = []
        for feature_group, cols in group_to_cols.items():
            partition_values = {
                "feature_group": feature_group,
                "asset_class": asset_class,
                "exchange": exchange,
                "frequency": frequency,
                "trading_date": trading_date,
                "instrument_id": instrument_id,
            }
            if legacy_layout:
                target_dir = (
                    Path(feature_root)
                    / f"feature_group={feature_group}"
                    / f"frequency={frequency}"
                    / f"trading_date={trading_date}"
                )
            else:
                target_dir = feature_data_path(feature_root, **partition_values)
            _prepare_partition(target_dir, mode)

            sub = df.select([*key_cols, *cols]).with_columns(
                _lit_cols(
                    **{k: partition_values[k] for k in partition_cols}
                )
            )
            table = sub.to_arrow()

            def _visit(f: "ds.WrittenFile") -> None:
                written.append(Path(f.path))

            ds.write_dataset(
                table,
                base_dir=str(feature_root),
                format="parquet",
                partitioning=list(partition_cols),
                partitioning_flavor="hive",
                existing_data_behavior="overwrite_or_ignore",
                basename_template=(
                    f"part-{uuid4().hex[:12]}-{{i}}.parquet"
                    if mode == "append"
                    else "part-{i}.parquet"
                ),
                file_visitor=_visit,
            )
            if manifest_root is not None:
                self._append_manifest(
                    manifest_root,
                    feature_group=feature_group,
                    cols=cols,
                    row_count=sub.height,
                    asset_class=asset_class,
                    exchange=exchange,
                    frequency=frequency,
                    trading_date=trading_date,
                    instrument_id=instrument_id,
                    legacy_layout=legacy_layout,
                )
        return written

    @staticmethod
    def _append_manifest(manifest_root, **kw: Any) -> None:
        """向 feature_manifest 追加一条记录（复用既有 Manifest）。"""
        from feature_engine.core import registry as _registry  # noqa: PLC0415
        from feature_engine.storage.metadata import Manifest, params_hash  # noqa: PLC0415

        manifest = Manifest(Path(manifest_root) / "feature_manifest")
        records = []
        for name in [n for n in kw["cols"]]:
            # 列名可能是特征 output；用其归属特征的 version。
            try:
                meta = _registry.get(name).meta
                version = meta.version
            except Exception:  # pragma: no cover - 派生列名无独立注册
                version = 1
            records.append(
                {
                    "partition_key": (
                        (
                            f"feature_group={kw['feature_group']}/"
                            f"frequency={kw['frequency']}/"
                            f"trading_date={kw['trading_date']}"
                        )
                        if kw.get("legacy_layout")
                        else (
                            f"feature_group={kw['feature_group']}/"
                            f"asset_class={kw['asset_class']}/exchange={kw['exchange']}/"
                            f"frequency={kw['frequency']}/"
                            f"trading_date={kw['trading_date']}/"
                            f"instrument_id={kw['instrument_id']}"
                        )
                    ),
                    "feature_name": name,
                    "version": version,
                    "params_hash": params_hash({"feature": name}),
                    "row_count": int(kw["row_count"]),
                    "source": "historical-builder",
                }
            )
        if records:
            manifest.append(records)


def _lit_cols(**values: str):
    """构造一组 ``pl.lit(...).alias(k)``（polars 懒加载）。"""
    import polars as pl  # noqa: PLC0415

    return [pl.lit(v).alias(k) for k, v in values.items()]


def _prepare_partition(path: Path, mode: str) -> None:
    """Apply write mode before pyarrow writes a partition."""
    if mode == "error" and path.exists() and any(path.glob("*.parquet")):
        raise FileExistsError(f"Partition {path} is non-empty and mode='error'")
    if mode == "overwrite" and path.exists():
        for part in path.glob("*.parquet"):
            part.unlink()


__all__ = ["HistoricalFeatureBuilder"]
