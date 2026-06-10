"""DEPRECATED compatibility shim — use ``strategy_framework.registry``."""
from strategy_framework.registry import STRATEGY_REGISTRY, get_entry

__all__ = ["STRATEGY_REGISTRY", "get_entry"]
