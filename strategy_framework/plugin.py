"""Strategy plugin descriptor.

A :class:`StrategyPlugin` is the contract a strategy package exposes so the
shared runner can build and run it. Each strategy defines one ``PLUGIN`` at the
bottom of its ``strategy.py`` and registers it in
:mod:`strategy_framework.registry`.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from nautilus_ext.features.api import FeatureSpec


@dataclass(frozen=True)
class StrategyPlugin:
    """Everything the shared runner needs to run one strategy."""

    name: str
    config_cls: type
    strategy_cls: type
    build_specs: Callable[[object], list[FeatureSpec]]
    default_config_path: str | None = None
