"""Explicit strategy registry.

Maps a short strategy name (used in configs and ``--strategy``) to the three
things the shared runner needs: the config dataclass, the strategy class, and
the ``build_specs`` factory.

Registration is **explicit** — to add a strategy, import its module and add one
entry below. There is no auto-discovery (kept deliberately simple).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from feature_strategies.strategies import ma_crossover
from nautilus_ext.features.api import FeatureSpec


@dataclass(frozen=True)
class StrategyEntry:
    """Everything the shared runner needs to run one strategy."""

    config_cls: type
    strategy_cls: type
    build_specs: Callable[[object], list[FeatureSpec]]


STRATEGY_REGISTRY: dict[str, StrategyEntry] = {
    "ma_crossover": StrategyEntry(
        config_cls=ma_crossover.MovingAverageCrossoverConfig,
        strategy_cls=ma_crossover.MovingAverageCrossoverStrategy,
        build_specs=ma_crossover.build_specs,
    ),
}


def get_entry(name: str) -> StrategyEntry:
    """Look up a strategy by name, with a helpful error listing valid names."""
    try:
        return STRATEGY_REGISTRY[name]
    except KeyError:
        valid = ", ".join(sorted(STRATEGY_REGISTRY)) or "(none registered)"
        raise KeyError(f"Unknown strategy {name!r}. Registered strategies: {valid}") from None
