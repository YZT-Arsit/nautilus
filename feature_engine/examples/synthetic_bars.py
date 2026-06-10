"""Compatibility re-export.

``BarEvent``, ``make_bars`` and ``ONE_SECOND_NS`` now live in the canonical data
layer (:mod:`data_engine`). This module re-exports them so existing
imports keep working; prefer importing from ``data_engine`` in new code.
"""
from data_engine.adapters.bar_adapter import make_bars
from data_engine.events import BarEvent
from data_engine.time import ONE_SECOND_NS

__all__ = ["BarEvent", "make_bars", "ONE_SECOND_NS"]
