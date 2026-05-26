"""Remove only allowlisted engineering-test artifacts beneath the project directory."""

from __future__ import annotations

import argparse
from pathlib import Path
import shutil


PROJECT_ROOT = Path(__file__).resolve().parents[1]
OUTPUT_DIRS = [
    "flow_batch_features",
    "flow_stream_features",
    "feature_states",
    "state_restore_check",
    "vwm_generated_bars",
    "redis_state_tests",
]
TEMP_PATTERNS = [
    "codex_remote_*.patch",
    "codex_remote_*.zip",
    "codex_remote_*.tar",
    "codex_remote_*.tar.gz",
]


def main() -> None:
    parser = argparse.ArgumentParser(description="Clean allowlisted internal engineering outputs.")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    targets = [PROJECT_ROOT / "outputs" / name for name in OUTPUT_DIRS]
    for pattern in TEMP_PATTERNS:
        targets.extend(PROJECT_ROOT.glob(pattern))
    for path in targets:
        if not path.exists():
            continue
        action = "would_delete" if args.dry_run else "deleted"
        print(f"{action}: {path}")
        if args.dry_run:
            continue
        if path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink()


if __name__ == "__main__":
    main()
