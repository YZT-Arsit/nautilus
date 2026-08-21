#!/usr/bin/env python3
"""Create the compact Phase 2.2B review bundle without large episode CSVs."""
from __future__ import annotations

import argparse
import zipfile
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path)
    parser.add_argument("archive", type=Path)
    args = parser.parse_args()
    top_level_csv = {
        "canonical_summary.csv",
        "direction_validation_summary.csv",
    }
    selected = [
        path for path in args.root.rglob("*")
        if path.is_file()
        and (
            path.suffix.lower() in {".png", ".json", ".parquet", ".yaml", ".html"}
            or (path.parent == args.root and path.name in top_level_csv)
        )
    ]
    temporary = args.archive.with_suffix(args.archive.suffix + ".tmp")
    args.archive.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for path in sorted(selected):
            archive.write(path, path.relative_to(args.root))
    temporary.replace(args.archive)
    print(f"files={len(selected)} bytes={args.archive.stat().st_size} archive={args.archive}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
