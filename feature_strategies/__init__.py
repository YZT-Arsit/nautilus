"""Top-level, user-facing strategy package.

Strategy authors work *here*. Each strategy is one module under
``feature_strategies/strategies/`` that declares the features it needs and the
logic turning feature snapshots into signals, using only the stable public API
(:mod:`nautilus_ext.features.api`).

All strategies share **one** runner — :mod:`feature_strategies.run_strategy` —
selected through the explicit :data:`feature_strategies.registry.STRATEGY_REGISTRY`.
There is no per-strategy run script. The low-level feature engine (operators,
backends, watermarks) lives under ``nautilus_ext/features/compute/`` and is not
edited to add a new strategy.
"""
from feature_strategies.registry import STRATEGY_REGISTRY, StrategyEntry, get_entry

__all__ = ["STRATEGY_REGISTRY", "StrategyEntry", "get_entry"]
