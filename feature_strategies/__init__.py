"""DEPRECATED compatibility shim.

The strategy framework moved to top-level packages:

* entry point   -> ``run_strategy.py``
* framework glue -> ``strategy_framework/``
* strategies    -> ``strategies/<name>/``

This package re-exports the registry for one transition cycle so old imports
keep working. Prefer the new locations in new code.
"""
from strategy_framework.registry import STRATEGY_REGISTRY, get_entry

__all__ = ["STRATEGY_REGISTRY", "get_entry"]
