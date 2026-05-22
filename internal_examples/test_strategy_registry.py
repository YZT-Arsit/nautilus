from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from nautilus_ext.strategies.strategy_registry import available_signal_engines
from nautilus_ext.strategies.strategy_registry import build_signal_engine


def test_available_signal_engines_includes_vwm_short():
    assert "vwm_short" in available_signal_engines()


def test_unknown_signal_engine_error_lists_available():
    try:
        build_signal_engine("unknown", {})
    except ValueError as exc:
        message = str(exc)
        assert "unknown" in message
        assert "vwm_short" in message
    else:
        raise AssertionError("build_signal_engine should reject unknown strategy_kind.")


def test_build_vwm_short_when_nautilus_runtime_available():
    try:
        engine = build_signal_engine(
            "vwm_short",
            {
                "mom_len": 5,
                "avg_len": 20,
                "atr_len": 5,
                "atr_pcnt": 0.5,
                "setup_len": 5,
            },
        )
    except ModuleNotFoundError as exc:
        if "nautilus_trader.core.data" in str(exc):
            print("Skipping vwm_short construction: Nautilus native module is not built.")
            return
        raise

    from nautilus_ext.strategies.vwm_short_signals import (
        VolumeWeightedMomentumShortSignalEngine,
    )

    assert isinstance(engine, VolumeWeightedMomentumShortSignalEngine)


def test_strategy_template_import_when_nautilus_runtime_available():
    try:
        from internal_examples.strategy_template import StrategyTemplate
    except ModuleNotFoundError as exc:
        if "nautilus_trader.core.data" in str(exc):
            print("Skipping StrategyTemplate construction: Nautilus native module is not built.")
            return
        raise

    try:
        strategy = StrategyTemplate(
            object(),
            strategy_kind="vwm_short",
            mom_len=5,
            avg_len=20,
            atr_len=5,
            atr_pcnt=0.5,
            setup_len=5,
            trade_size=1,
        )
    except ModuleNotFoundError as exc:
        if "nautilus_trader.core.data" in str(exc):
            print("Skipping StrategyTemplate construction: Nautilus native module is not built.")
            return
        raise

    assert strategy.strategy_kind == "vwm_short"


if __name__ == "__main__":
    test_available_signal_engines_includes_vwm_short()
    test_unknown_signal_engine_error_lists_available()
    test_build_vwm_short_when_nautilus_runtime_available()
    test_strategy_template_import_when_nautilus_runtime_available()
    print("strategy registry tests ok")
