from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from nautilus_ext.features.tradeblazer_features import RawMomentumFeature
from nautilus_ext.features.tradeblazer_features import cross_over
from nautilus_ext.features.tradeblazer_features import cross_under
from nautilus_ext.strategies.signal_types import BarInput

try:
    import pytest
except ImportError:  # pragma: no cover
    pytest = None


def vwm_feature_engine():
    try:
        from nautilus_ext.features.vwm_features import VwmFeatureConfig
        from nautilus_ext.features.vwm_features import VwmFeatureEngine
    except ModuleNotFoundError as exc:
        if "nautilus_trader.core.data" in str(exc):
            if pytest is not None:
                pytest.skip("Nautilus native module is not built.")
            print("Skipping VWM feature test: Nautilus native module is not built.")
            return None
        raise
    return VwmFeatureEngine(VwmFeatureConfig(mom_len=1, avg_len=2, atr_len=1))


def bar(open_, high, low, close, volume=1.0):
    return BarInput(
        open=float(open_),
        high=float(high),
        low=float(low),
        close=float(close),
        volume=float(volume),
    )


def test_tradeblazer_feature_helpers():
    momentum = RawMomentumFeature(period=3)
    assert momentum.update(10) is None
    assert momentum.update(11) is None
    assert momentum.update(13) is None
    assert momentum.update(16) == 6

    assert cross_over(-1, 1) is True
    assert cross_under(1, -1) is True


def test_vwm_features_keep_previous_values_and_crosses():
    engine = vwm_feature_engine()
    if engine is None:
        return
    first = engine.update(bar(10, 11, 9, 10))
    second = engine.update(bar(12, 13, 11, 12))
    third = engine.update(bar(9, 13, 9, 9))
    fourth = engine.update(bar(20, 21, 19, 20))

    assert first.momentum is None
    assert second.momentum == 2.0
    assert second.vwm is not None
    assert second.atr is not None
    assert third.prev_vwm == second.vwm
    assert third.prev_atr == second.atr
    assert third.bear_setup is True
    assert fourth.bull_setup is True


def test_vwm_feature_reset_supports_new_stream():
    engine = vwm_feature_engine()
    if engine is None:
        return
    engine.update(bar(10, 11, 9, 10))
    engine.update(bar(12, 13, 11, 12))
    engine.reset()

    reset_first = engine.update(bar(20, 21, 19, 20))
    assert reset_first.current_bar == 1
    assert reset_first.momentum is None
    assert reset_first.vwm is None


if __name__ == "__main__":
    test_tradeblazer_feature_helpers()
    test_vwm_features_keep_previous_values_and_crosses()
    test_vwm_feature_reset_supports_new_stream()
    print("feature tests ok")
