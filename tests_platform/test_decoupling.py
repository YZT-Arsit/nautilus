"""Contract: data_engine + feature_engine must stay framework-agnostic.

The data and feature layers must be portable to *any* framework — importing them
must never pull in ``nautilus_trader`` (all Nautilus integration lives in
``strategy_framework``). This is an offline, network-free guard.
"""
from __future__ import annotations

import importlib
import sys


def test_data_and_feature_layers_do_not_import_nautilus():
    # Import the two portable layers fresh and assert no Nautilus leaked in.
    for mod in ("data_engine", "feature_engine", "data_engine.loader"):
        importlib.import_module(mod)
    leaked = sorted(
        m for m in sys.modules
        if m == "nautilus_trader" or m.startswith("nautilus_trader.")
    )
    assert not leaked, f"data/feature layers leaked Nautilus imports: {leaked}"


def test_binance_ws_mode_is_registered():
    from data_engine.loader import _LOADERS

    assert "binance_ws" in _LOADERS
