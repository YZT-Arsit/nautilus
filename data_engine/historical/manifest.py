"""Append-only manifest for the historical data cache.

Each successful/skipped/verified/failed partition operation appends one JSON line
to ``<root>/../_catalog/manifest.jsonl`` -- a sibling of ``market_data`` so the
Parquet dataset root is never polluted with non-parquet files.

Pure stdlib (json/dataclasses); imports no ``pyarrow``, ``polars``,
``feature_engine`` or ``nautilus_trader``.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1
SOURCE = "binance_vision"

VALID_STATUS = {"downloaded", "skipped_existing", "verified", "failed"}


def catalog_dir_for_root(root: str | Path) -> Path:
    """``<root>/../_catalog`` -- sibling of the ``market_data`` dataset root.

    Uses the parent as given (no ``resolve()``) so the manifest path tracks the
    caller's root form -- important on platforms where tmp dirs are symlinked
    (e.g. macOS ``/var`` -> ``/private/var``).
    """
    return Path(root).parent / "_catalog"


def manifest_path_for_root(root: str | Path) -> Path:
    return catalog_dir_for_root(root) / "manifest.jsonl"


@dataclass
class ManifestRecord:
    """One manifest line. ``status`` is one of :data:`VALID_STATUS`."""

    status: str
    exchange: str
    venue_type: str
    symbol: str
    data_kind: str           # "bar" | "trade"
    date: str
    bar_type: str | None = None
    data_type: str | None = None
    source: str = SOURCE
    source_url: str | None = None
    local_path: str | None = None
    row_count: int | None = None
    ts_min: str | None = None
    ts_max: str | None = None
    file_size_bytes: int | None = None
    checksum: str | None = None
    overwrite: bool = False
    error: str | None = None
    schema_version: int = SCHEMA_VERSION
    created_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class Manifest:
    """Append-only JSONL manifest stored beside (not inside) ``market_data``."""

    def __init__(self, root: str | Path) -> None:
        self._path = manifest_path_for_root(root)

    @property
    def path(self) -> Path:
        return self._path

    def append(self, record: ManifestRecord | dict[str, Any], *, now: str | None = None) -> dict[str, Any]:
        """Append one record; fills ``created_at``/``schema_version`` if absent.

        Writing only ever touches ``_catalog`` -- it never modifies any parquet.
        """
        if isinstance(record, ManifestRecord):
            d = record.to_dict()
        else:
            d = dict(record)
        status = d.get("status")
        if status not in VALID_STATUS:
            raise ValueError(f"invalid manifest status {status!r}; must be one of {sorted(VALID_STATUS)}")
        if d.get("created_at") is None:
            d["created_at"] = now if now is not None else datetime.utcnow().isoformat()
        if d.get("schema_version") is None:
            d["schema_version"] = SCHEMA_VERSION
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with open(self._path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(d, ensure_ascii=False) + "\n")
        return d

    def read_all(self) -> list[dict[str, Any]]:
        if not self._path.exists():
            return []
        out: list[dict[str, Any]] = []
        with open(self._path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    out.append(json.loads(line))
        return out
