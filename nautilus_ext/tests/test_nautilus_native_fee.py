"""Tests that the native backend applies config fee_rate to the instrument.

Requires ``nautilus_trader`` (present on the backtest server); skipped where it
is unavailable. No network, no account, no orders - only instrument construction.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

pytest.importorskip("nautilus_trader")

from nautilus_trader.test_kit.providers import TestInstrumentProvider

from strategy_framework.backends.nautilus_native import _instrument_with_fees


def _btcusdt():
    return TestInstrumentProvider.btcusdt_binance()


def test_default_instrument_fee_is_not_config_rate():
    # Baseline: the bundled test instrument carries its own (non-5bps) fees.
    inst = _btcusdt()
    assert abs(float(inst.taker_fee) - 0.0005) > 1e-9  # not already 5 bps


def test_fee_rate_applied_to_maker_and_taker():
    inst = _instrument_with_fees(_btcusdt(), 0.0005)
    assert abs(float(inst.taker_fee) - 0.0005) < 1e-12
    assert abs(float(inst.maker_fee) - 0.0005) < 1e-12


def test_fee_rate_is_configurable():
    a = _instrument_with_fees(_btcusdt(), 0.0005)
    b = _instrument_with_fees(_btcusdt(), 0.001)
    assert abs(float(a.taker_fee) - 0.0005) < 1e-12
    assert abs(float(b.taker_fee) - 0.001) < 1e-12
    assert float(a.taker_fee) != float(b.taker_fee)


def test_other_instrument_fields_preserved():
    base = _btcusdt()
    inst = _instrument_with_fees(base, 0.0005)
    assert inst.id == base.id
    assert inst.price_precision == base.price_precision
    assert inst.size_precision == base.size_precision
    assert inst.quote_currency == base.quote_currency
