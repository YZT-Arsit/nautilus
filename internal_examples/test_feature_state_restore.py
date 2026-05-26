from dataclasses import asdict
from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from nautilus_ext.strategies.signal_types import BarInput

try:
    import pytest
except ImportError:  # pragma: no cover
    pytest = None


def engine():
    try:
        from nautilus_ext.features.vwm_features import VwmFeatureConfig
        from nautilus_ext.features.vwm_features import VwmFeatureEngine
    except ModuleNotFoundError as exc:
        if "nautilus_trader.core.data" in str(exc):
            if pytest is not None:
                pytest.skip("Nautilus native module is not built.")
            return None
        raise
    return VwmFeatureEngine(VwmFeatureConfig(mom_len=2, avg_len=3, atr_len=2))


def bars():
    closes = [10, 12, 11, 8, 9, 14, 13, 7]
    return [
        BarInput(open=float(close), high=float(close + 1), low=float(close - 1), close=float(close), volume=2.0)
        for close in closes
    ]


def test_vwm_feature_state_restore_matches_continuous_updates():
    full = engine()
    partial = engine()
    restored = engine()
    if full is None or partial is None or restored is None:
        return
    sequence = bars()
    expected = [full.update(bar) for bar in sequence]
    for bar in sequence[:4]:
        partial.update(bar)
    checkpoint = partial.state_dict()

    required_fields = {
        "current_bar",
        "config",
        "momentum_state",
        "current_vwm",
        "previous_vwm",
        "current_atr",
        "previous_atr",
        "atr_true_range_window",
        "atr_previous_close",
    }
    assert required_fields.issubset(checkpoint)
    restored.load_state_dict(checkpoint)
    actual = [restored.update(bar) for bar in sequence[4:]]
    assert [asdict(snapshot) for snapshot in actual] == [
        asdict(snapshot) for snapshot in expected[4:]
    ]


def test_vwm_feature_restore_rejects_different_config():
    source = engine()
    if source is None:
        return
    source.update(bars()[0])
    try:
        from nautilus_ext.features.vwm_features import VwmFeatureConfig
        from nautilus_ext.features.vwm_features import VwmFeatureEngine
        incompatible = VwmFeatureEngine(VwmFeatureConfig(mom_len=3, avg_len=3, atr_len=2))
        incompatible.load_state_dict(source.state_dict())
    except ValueError as exc:
        assert "config" in str(exc)
    else:
        raise AssertionError("An incompatible feature config must reject restore.")
