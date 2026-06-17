"""Historical data manager / local cache for the self-owned ``data_engine`` layer.

Downloads Binance Vision historical archives into a local Hive-partitioned
``market_data`` root, tracks coverage in a sibling ``_catalog/manifest.jsonl``,
and validates cached partitions -- so backtests read local Parquet instead of
re-downloading.  Imports no ``nautilus_trader``; Binance Vision is a *historical
archive* source, not a live feed.
"""
from data_engine.historical.catalog import (
    LocalDataCatalog,
    Partition,
    partition_dir,
    partition_relpath,
)
from data_engine.historical.downloader import (
    BinanceVisionHistoricalDownloader,
    DownloadResult,
    default_fetcher,
)
from data_engine.historical.manifest import (
    Manifest,
    ManifestRecord,
    catalog_dir_for_root,
    manifest_path_for_root,
)
from data_engine.historical.plan import (
    DownloadPlan,
    PlannedPartition,
    build_plan,
    generate_dates,
)
from data_engine.historical.validators import ValidationResult, validate_partition

__all__ = [
    "LocalDataCatalog",
    "Partition",
    "partition_dir",
    "partition_relpath",
    "DownloadPlan",
    "PlannedPartition",
    "build_plan",
    "generate_dates",
    "Manifest",
    "ManifestRecord",
    "catalog_dir_for_root",
    "manifest_path_for_root",
    "BinanceVisionHistoricalDownloader",
    "DownloadResult",
    "default_fetcher",
    "ValidationResult",
    "validate_partition",
]
