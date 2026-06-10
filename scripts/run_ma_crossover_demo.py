#!/usr/bin/env python3
"""Backward-compatible wrapper for the MA crossover demo.

The framework moved to top-level packages. Prefer:

    python run_strategy.py --strategy ma_crossover

This wrapper forwards to the top-level shared runner with the MA crossover
config so the historical entry point keeps working.
"""
import sys
from pathlib import Path

# Allow direct execution (``python scripts/run_ma_crossover_demo.py``) by putting
# the repository root on the path before importing the top-level runner.
_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from run_strategy import main  # noqa: E402

if __name__ == "__main__":
    main(["--config", "strategies/ma_crossover/config.yaml"])
