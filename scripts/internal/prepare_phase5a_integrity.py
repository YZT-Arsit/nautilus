#!/usr/bin/env python3
"""Capture an additive Phase 5A integrity baseline without reading Parquet bodies.

Prior research/config/source files are content hashed.  Canonical market and
feature stores are protected with a deterministic inventory digest (relative
path + size), which is cheap enough to repeat after Phase 5A and detects any
partition addition, removal, or size mutation.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT = ROOT / "outputs/internal_audit/strategy_workbook/phase5a_protected_hashes_before.json"

CONTENT_ROOTS = (
    "strategies",
    "strategy_framework/modules",
    "configs/semantic_contracts",
    "outputs/internal_audit/strategy_workbook",
    "outputs/deliverables/phase2",
    "outputs/deliverables/phase3",
    "outputs/deliverables/phase3b_wave1",
    "outputs/deliverables/phase3b_wave3",
    "outputs/deliverables/phase3b_wave5",
    "outputs/deliverables/phase3c",
    "outputs/deliverables/phase4a",
    "outputs/deliverables/phase4b",
    "outputs/deliverables/phase4c",
)
INVENTORY_ROOTS = (
    "historical_data/market_data",
    "historical_data/feature_data",
)
SKIP_NAMES = {
    "phase5a_protected_hashes_before.json",
    "phase5a_protected_hashes_after.json",
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def iter_files(root: Path):
    if root.is_file():
        yield root
    elif root.is_dir():
        for path in sorted(root.rglob("*")):
            if path.is_file() and "__pycache__" not in path.parts and path.name not in SKIP_NAMES:
                yield path


def content_snapshot() -> dict[str, object]:
    files: dict[str, dict[str, object]] = {}
    for relative_root in CONTENT_ROOTS:
        root = ROOT / relative_root
        for path in iter_files(root):
            relative = path.relative_to(ROOT).as_posix()
            # Phase 5A is additive inside the common audit tree; exclude only
            # its own artifacts from the prior-phase baseline.
            if "/phase5a_" in relative or Path(relative).name.startswith("phase5a_"):
                continue
            files[relative] = {"size": path.stat().st_size, "sha256": sha256(path)}
    digest = hashlib.sha256()
    for relative, metadata in sorted(files.items()):
        digest.update(f"{relative}\0{metadata['size']}\0{metadata['sha256']}\n".encode())
    return {"file_count": len(files), "digest": digest.hexdigest(), "files": files}


def inventory_snapshot(relative_root: str) -> dict[str, object]:
    root = ROOT / relative_root
    digest = hashlib.sha256()
    count = size = 0
    first = last = None
    for path in iter_files(root):
        relative = path.relative_to(root).as_posix()
        stat = path.stat()
        digest.update(f"{relative}\0{stat.st_size}\n".encode())
        count += 1
        size += stat.st_size
        first = relative if first is None else first
        last = relative
    return {
        "root": relative_root,
        "file_count": count,
        "total_size": size,
        "inventory_digest": digest.hexdigest(),
        "first_path": first,
        "last_path": last,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    payload = {
        "schema_version": 1,
        "captured_at_utc": datetime.now(timezone.utc).isoformat(),
        "repository_root": str(ROOT),
        "content": content_snapshot(),
        "data_inventories": {
            root: inventory_snapshot(root) for root in INVENTORY_ROOTS
        },
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_suffix(args.output.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, args.output)
    print(json.dumps({
        "output": str(args.output),
        "content_files": payload["content"]["file_count"],
        "content_digest": payload["content"]["digest"],
        "data_inventories": payload["data_inventories"],
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
