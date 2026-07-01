"""Hive partition path construction and parsing.

We keep path construction entirely in this module so writers and readers stay
in sync. The Hive convention is ``key=value`` segments joined by ``/``; PyArrow
Dataset uses exactly the same convention so we get free predicate pruning.

Examples
--------
Raw bar partition::

    data/raw/asset_class=stock/exchange=SSE/frequency=1m/trading_date=2026-05-26/

Feature partition::

    data/features/feature_group=technical/frequency=1m/trading_date=2026-05-26/
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

_KV_PATTERN = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)=(.+)$")


@dataclass(frozen=True)
class PartitionKey:
    """A set of Hive partition values. Order-preserving so paths are stable."""

    values: tuple[tuple[str, str], ...]

    @classmethod
    def from_dict(cls, d: dict[str, str], order: tuple[str, ...]) -> "PartitionKey":
        """Build a key from a dict + a column ordering. Missing keys raise."""
        try:
            return cls(tuple((k, str(d[k])) for k in order))
        except KeyError as e:
            raise KeyError(f"Missing partition column {e.args[0]!r} in {d}") from e

    def to_path(self, root: Path | str) -> Path:
        """Append ``key=value`` segments to ``root``."""
        p = Path(root)
        for k, v in self.values:
            p = p / f"{k}={v}"
        return p

    def to_str(self) -> str:
        return "/".join(f"{k}={v}" for k, v in self.values)


def parse_partition_path(path: Path | str) -> dict[str, str]:
    """Walk a path and extract every ``key=value`` segment.

    Useful for the manifest writer: given the file we just wrote, recover its
    logical partition without needing the caller to pass it twice.
    """
    out: dict[str, str] = {}
    for part in Path(path).parts:
        m = _KV_PATTERN.match(part)
        if m:
            out[m.group(1)] = m.group(2)
    return out


# ---------------------------------------------------------------------------
# 统一历史数据体系（historical_data）—— market_data 与 feature_data 平级
# ---------------------------------------------------------------------------
#
# 锁定布局（见 docs/PLATFORM_ARCHITECTURE.md §2.3）。data_type / symbol / freq
# 均为一等分区维度，venue_type 保留（区分 spot / futures_um / futures_cm）：
#
#   historical_data/
#       market_data/  asset_class/exchange/venue_type/symbol/data_type/freq/date/
#       feature_data/ feature_group/asset_class/exchange/venue_type/symbol/freq/date/
#       instruments/  exchange/as_of_date/
#       manifests/    dataset_manifest/ , feature_manifest/
#
# 这些都是**纯路径构造**（标准库），writer/reader 共享，保证读写分区一致。

MARKET_DATA_PARTITION_COLS = (
    "asset_class",
    "exchange",
    "venue_type",
    "symbol",
    "data_type",
    "freq",
    "date",
)

# feature_data 镜像 market_data（特征即数据）。feature_group 为逻辑分组顶层维度；
# 无 data_type（特征本身就是产物），其余维度与 market_data 对齐。
FEATURE_DATA_PARTITION_COLS = (
    "feature_group",
    "asset_class",
    "exchange",
    "venue_type",
    "symbol",
    "freq",
    "date",
)

INSTRUMENTS_PARTITION_COLS = ("exchange", "as_of_date")

# historical_data 根下的子目录名。
MARKET_DATA_SUBDIR = "market_data"
FEATURE_DATA_SUBDIR = "feature_data"
INSTRUMENTS_SUBDIR = "instruments"
MANIFESTS_SUBDIR = "manifests"


def market_data_path(
    root: Path | str,
    *,
    asset_class: str,
    exchange: str,
    venue_type: str,
    symbol: str,
    data_type: str,
    freq: str,
    date: str,
) -> Path:
    """构造一条 market_data Hive 分区目录（锁定布局）。"""
    key = PartitionKey.from_dict(
        {
            "asset_class": asset_class,
            "exchange": exchange,
            "venue_type": venue_type,
            "symbol": symbol,
            "data_type": data_type,
            "freq": freq,
            "date": date,
        },
        MARKET_DATA_PARTITION_COLS,
    )
    return key.to_path(root)


def feature_data_path(
    root: Path | str,
    *,
    feature_group: str,
    asset_class: str,
    exchange: str,
    venue_type: str,
    symbol: str,
    freq: str,
    date: str,
) -> Path:
    """构造一条 feature_data Hive 分区目录（锁定布局）。"""
    key = PartitionKey.from_dict(
        {
            "feature_group": feature_group,
            "asset_class": asset_class,
            "exchange": exchange,
            "venue_type": venue_type,
            "symbol": symbol,
            "freq": freq,
            "date": date,
        },
        FEATURE_DATA_PARTITION_COLS,
    )
    return key.to_path(root)


def instruments_path(
    root: Path | str,
    *,
    exchange: str,
    as_of_date: str,
) -> Path:
    """构造一条 instruments Hive 分区目录。"""
    key = PartitionKey.from_dict(
        {"exchange": exchange, "as_of_date": as_of_date},
        INSTRUMENTS_PARTITION_COLS,
    )
    return key.to_path(root)
