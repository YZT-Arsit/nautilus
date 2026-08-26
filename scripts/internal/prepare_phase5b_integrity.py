#!/usr/bin/env python3
"""Freeze Phase 5A/runtime and canonical-data state before Phase 5B changes."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
AUDIT = ROOT / "outputs/internal_audit/strategy_workbook"
CONTENT_ROOTS = (
    "strategies", "strategy_framework", "feature_engine", "data_engine",
    "configs/semantic_contracts", "outputs/internal_audit/strategy_workbook",
    "outputs/deliverables", "outputs/batches/workbook_strategies_phase5a",
)
INVENTORY_ROOTS = ("historical_data/market_data", "historical_data/feature_data")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def files(root: Path):
    if root.is_file():
        yield root
    elif root.is_dir():
        for path in sorted(root.rglob("*")):
            if path.is_file() and "__pycache__" not in path.parts and not path.name.startswith("phase5b_"):
                yield path


def content() -> dict[str, object]:
    items: dict[str, dict[str, object]] = {}
    for relative_root in CONTENT_ROOTS:
        for path in files(ROOT / relative_root):
            relative = path.relative_to(ROOT).as_posix()
            items[relative] = {"size": path.stat().st_size, "sha256": sha256(path)}
    digest = hashlib.sha256()
    for relative, metadata in sorted(items.items()):
        digest.update(f"{relative}\0{metadata['size']}\0{metadata['sha256']}\n".encode())
    return {"file_count": len(items), "digest": digest.hexdigest(), "files": items}


def inventory(relative_root: str) -> dict[str, object]:
    root = ROOT / relative_root
    digest = hashlib.sha256(); count = size = 0
    for path in files(root):
        stat = path.stat(); relative = path.relative_to(root).as_posix()
        digest.update(f"{relative}\0{stat.st_size}\n".encode())
        count += 1; size += stat.st_size
    return {"root": relative_root, "file_count": count, "total_size": size,
            "inventory_digest": digest.hexdigest()}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=AUDIT / "phase5b_protected_hashes_before.json")
    args = parser.parse_args()
    payload = {
        "schema_version": 1,
        "captured_at_utc": datetime.now(timezone.utc).isoformat(),
        "content": content(),
        "data_inventories": {root: inventory(root) for root in INVENTORY_ROOTS},
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_suffix(args.output.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, args.output)
    print(json.dumps({"output": str(args.output), "content_files": payload["content"]["file_count"],
                      "content_digest": payload["content"]["digest"],
                      "data_inventories": payload["data_inventories"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
