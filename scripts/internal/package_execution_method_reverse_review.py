#!/usr/bin/env python3
"""Hash-manifest and ZIP the validated execution/reverse delivery."""

from __future__ import annotations

import argparse
import hashlib
import json
import zipfile
from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parents[2]
DELIVERY = ROOT / "outputs/deliverables/execution_method_and_reverse_review"


def sha256(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(8*1024*1024), b""):
            value.update(chunk)
    return value.hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--delivery", type=Path, default=DELIVERY)
    args = parser.parse_args()
    delivery = args.delivery.resolve()
    validation = json.loads((delivery / "validation_summary.json").read_text())
    if validation["status"] != "PASSED":
        raise ValueError("delivery validation is not PASSED")
    files = sorted(
        path for path in delivery.rglob("*")
        if path.is_file() and path.name != "artifact_manifest.csv"
    )
    rows = [
        {"relative_path":path.relative_to(delivery).as_posix(), "bytes":path.stat().st_size, "sha256":sha256(path)}
        for path in files
    ]
    pd.DataFrame(rows).to_csv(delivery / "artifact_manifest.csv", index=False)
    archive = delivery.with_suffix(".zip")
    temporary = archive.with_suffix(".zip.tmp")
    if temporary.exists():
        temporary.unlink()
    with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6, allowZip64=True) as zipped:
        for path in sorted(path for path in delivery.rglob("*") if path.is_file()):
            zipped.write(path, Path(delivery.name) / path.relative_to(delivery))
    temporary.replace(archive)
    with zipfile.ZipFile(archive) as zipped:
        if zipped.testzip() is not None:
            raise ValueError("ZIP integrity failed")
    summary = {"status":"PASSED", "files":sum(path.is_file() for path in delivery.rglob('*')), "zip":str(archive), "zip_sha256":sha256(archive), "zip_bytes":archive.stat().st_size}
    (delivery.parent / "execution_method_and_reverse_review_package.json").write_text(json.dumps(summary, indent=2)+"\n")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
