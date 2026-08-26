#!/usr/bin/env python3
"""Create an auditable, self-contained episode-diagnostics review package."""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CANONICAL_SOURCE = ROOT / "outputs/deliverables/existing_registered_strategies_current"
DEFAULT_DIAGNOSTICS_SOURCE = (
    ROOT / "outputs/deliverables/existing_registered_strategies_corrected"
)
DEFAULT_DESTINATION = (
    ROOT / "outputs/deliverables/existing_registered_strategies_corrected_review.zip"
)
ROOT_FILES = (
    "canonical_summary.csv",
    "canonical_summary.html",
    "validation_summary.json",
    "episode_metric_summary.csv",
    "episode_distribution_bins.csv",
    "episode_diagnostics_summary.html",
    "episode_diagnostics_validation.json",
    "episode_diagnostics_artifact_manifest.json",
    "episode_render_missing_audit.csv",
    "phase_episode_plot_validation.csv",
    "episode_plot_validation.csv",
    "wrong_direction_tree_audit.csv",
    "original_strategy_intrinsic_direction.csv",
    "old_direction_filter_redundancy.csv",
    "old_512_zero_episode_reclassification.csv",
    "strict_reverse_validation.csv",
    "boss_direction_model_migration.csv",
    "boss_direction_model_audit.json",
    "server_wrong_direction_cleanup_manifest.csv",
)
DIAGNOSTIC_RUN_PATTERNS = (
    "*_episode_diagnostics.png",
    "episode_distribution_bins.csv",
    "episode_metric_summary.json",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--canonical-source", type=Path, default=DEFAULT_CANONICAL_SOURCE)
    parser.add_argument("--diagnostics-source", type=Path, default=DEFAULT_DIAGNOSTICS_SOURCE)
    parser.add_argument("--destination", type=Path, default=DEFAULT_DESTINATION)
    return parser.parse_args()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(4 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def discover(canonical_source: Path, diagnostics_source: Path) -> list[tuple[Path, Path]]:
    paths: list[tuple[Path, Path]] = [
        (diagnostics_source / name, Path(name)) for name in ROOT_FILES
    ]
    paths.extend(
        (path, path.relative_to(diagnostics_source))
        for path in diagnostics_source.glob("*/BTCUSDT/*/lag*m/*/*_performance.png")
    )
    for pattern in DIAGNOSTIC_RUN_PATTERNS:
        paths.extend(
            (path, path.relative_to(diagnostics_source))
            for path in diagnostics_source.glob(f"*/BTCUSDT/*/lag*m/*/{pattern}")
        )
    paths = sorted(
        {(path.resolve(), relative) for path, relative in paths if path.is_file()},
        key=lambda item: item[1].as_posix(),
    )
    validation = json.loads(
        (diagnostics_source / "episode_diagnostics_validation.json").read_text(encoding="utf-8")
    )
    expected_units = int(validation["result_unit_count"])
    expected = {
        "diagnostic_figures": expected_units,
        "performance_figures": expected_units,
        "run_bin_tables": expected_units,
        "run_summaries": expected_units,
    }
    observed = {
        "diagnostic_figures": sum(path.name.endswith("_episode_diagnostics.png") for path, _ in paths),
        "performance_figures": sum(path.name.endswith("_performance.png") for path, _ in paths),
        "run_bin_tables": sum(
            path.name == "episode_distribution_bins.csv" and relative.parent != Path(".")
            for path, relative in paths
        ),
        "run_summaries": sum(path.name == "episode_metric_summary.json" for path, _ in paths),
    }
    if observed != expected:
        raise RuntimeError(f"incomplete package source: observed={observed}, expected={expected}")
    missing_root = [name for name in ROOT_FILES if not (diagnostics_source / name).is_file()]
    if missing_root:
        raise RuntimeError(f"missing central artifacts: {missing_root}")
    return paths


def manifest_bytes(paths: list[tuple[Path, Path]]) -> bytes:
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=("relative_path", "size_bytes", "sha256"))
    writer.writeheader()
    for path, relative in paths:
        writer.writerow(
            {
                "relative_path": relative.as_posix(),
                "size_bytes": path.stat().st_size,
                "sha256": sha256(path),
            }
        )
    return buffer.getvalue().encode("utf-8")


def main() -> int:
    args = parse_args()
    canonical_source = args.canonical_source.resolve()
    diagnostics_source = args.diagnostics_source.resolve()
    destination = args.destination.resolve()
    paths = discover(canonical_source, diagnostics_source)
    manifest = manifest_bytes(paths)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".part")
    temporary.unlink(missing_ok=True)
    with zipfile.ZipFile(
        temporary, "w", compression=zipfile.ZIP_STORED, allowZip64=True
    ) as archive:
        archive.writestr("episode_diagnostics_package_manifest.csv", manifest)
        for path, relative in paths:
            archive.write(path, relative.as_posix())
    temporary.replace(destination)
    digest = sha256(destination)
    destination.with_suffix(destination.suffix + ".sha256").write_text(
        f"{digest}  {destination.name}\n", encoding="ascii"
    )
    with zipfile.ZipFile(destination) as archive:
        bad_member = archive.testzip()
        if bad_member is not None:
            raise RuntimeError(f"ZIP integrity failure: {bad_member}")
    print(
        f"PACKAGE path={destination} files={len(paths) + 1} "
        f"bytes={destination.stat().st_size} sha256={digest} integrity=passed"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
