#!/usr/bin/env python3
"""
Compatibility entrypoint.

The A/B runner has been generalized to N-strategy independent comparison.
Prefer run_user_strategies.py for normal use, or run_multi_strategy_comparison.py
for a three-strategy example.
"""

from pathlib import Path
import runpy
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))


if __name__ == "__main__":
    print(
        "run_strategy_ab_comparison.py is kept for compatibility. "
        "Use run_user_strategies.py as the main entrypoint."
    )
    runpy.run_path(
        str(Path(__file__).with_name("run_multi_strategy_comparison.py")),
        run_name="__main__",
    )
