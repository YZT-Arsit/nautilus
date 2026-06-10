#!/usr/bin/env python3
"""Backward-compatible wrapper for the MA crossover demo.

Strategies now share one runner. Prefer:

    python -m feature_strategies.run_strategy --config feature_strategies/configs/ma_crossover.yaml

This wrapper forwards to that shared runner with the MA crossover config so the
historical entry point keeps working.
"""
from feature_strategies.run_strategy import main

if __name__ == "__main__":
    main(["--config", "feature_strategies/configs/ma_crossover.yaml"])
