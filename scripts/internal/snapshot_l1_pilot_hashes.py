#!/usr/bin/env python3
"""Create an external hash snapshot of the immutable historical L1 pilot tree."""

from __future__ import annotations

import argparse
import csv
import hashlib
import os
from pathlib import Path


def digest(path: Path) -> str:
    result = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            result.update(chunk)
    return result.hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    source = args.source.resolve()
    rows = []
    for path in sorted(p for p in source.rglob("*") if p.is_file()):
        rows.append(
            {
                "relative_path": path.relative_to(source).as_posix(),
                "bytes": path.stat().st_size,
                "sha256": digest(path),
            }
        )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_suffix(args.output.suffix + ".tmp")
    with temporary.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["relative_path", "bytes", "sha256"])
        writer.writeheader()
        writer.writerows(rows)
    os.replace(temporary, args.output)
    print(f"files={len(rows)} bytes={sum(row['bytes'] for row in rows)}")


if __name__ == "__main__":
    main()
