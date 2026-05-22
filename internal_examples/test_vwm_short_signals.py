from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from nautilus_ext.strategies.signal_types import BarInput
from nautilus_ext.strategies.tradeblazer_helpers import MomentumState
from nautilus_ext.strategies.tradeblazer_helpers import cross_over
from nautilus_ext.strategies.tradeblazer_helpers import cross_under
from nautilus_ext.strategies.vwm_short_signals import VwmShortSignalConfig
from nautilus_ext.strategies.vwm_short_signals import VolumeWeightedMomentumShortSignalEngine


def bar(open_, high, low, close, volume=1.0):
    return BarInput(
        open=float(open_),
        high=float(high),
        low=float(low),
        close=float(close),
        volume=float(volume),
    )


def test_tradeblazer_helpers():
    momentum = MomentumState(period=3)
    assert momentum.update(10) is None
    assert momentum.update(11) is None
    assert momentum.update(13) is None
    assert momentum.update(16) == 6

    assert cross_over(-1, 1, 0) is True
    assert cross_over(1, 2, 0) is False
    assert cross_under(1, -1, 0) is True
    assert cross_under(-1, -2, 0) is False


def test_entry_setup_and_trigger():
    engine = VolumeWeightedMomentumShortSignalEngine(
        VwmShortSignalConfig(mom_len=1, avg_len=2, atr_len=1, atr_pcnt=0.5, setup_len=2),
    )
    engine.update(bar(10, 11, 9, 10))
    engine.update(bar(12, 13, 11, 12))
    bear = engine.update(bar(9, 13, 9, 9))
    entry = engine.update(bar(7, 8, 6, 7))

    assert bear.debug["bear_setup"] is True
    assert bear.debug["se_price"] == 9.0
    assert bear.debug["s_setup"] == 0
    assert bear.entry_side is None
    assert entry.entry_side == "SELL"
    assert entry.entry_order_type == "stop_market"
    assert entry.entry_price == 7.0
    assert entry.debug["entry_signal"] is True


def test_cancel_entry_after_setup_expires():
    engine = VolumeWeightedMomentumShortSignalEngine(
        VwmShortSignalConfig(mom_len=1, avg_len=2, atr_len=1, atr_pcnt=0.5, setup_len=1),
    )
    engine.update(bar(10, 11, 9, 10))
    engine.update(bar(12, 13, 11, 12))
    engine.update(bar(9, 13, 9, 9))
    active_1 = engine.update(bar(10, 11, 9, 10))
    active_2 = engine.update(bar(11, 12, 10, 11))
    cancel = engine.update(bar(12, 13, 11, 12))

    assert active_1.entry_side == "SELL"
    assert active_2.entry_side == "SELL"
    assert cancel.cancel_entry is True
    assert cancel.entry_side is None


def test_exit_uses_previous_bull_setup():
    engine = VolumeWeightedMomentumShortSignalEngine(
        VwmShortSignalConfig(mom_len=1, avg_len=2, atr_len=1, atr_pcnt=0.5, setup_len=2),
    )
    engine.update(bar(10, 11, 9, 10))
    engine.update(bar(12, 13, 11, 12))
    engine.update(bar(9, 13, 9, 9))
    engine.update(bar(7, 8, 6, 7), position=-1, bars_since_entry=0)
    bull = engine.update(bar(20, 21, 19, 20), position=-1, bars_since_entry=1)
    exit_signal = engine.update(bar(20, 21, 19, 20), position=-1, bars_since_entry=2)

    assert bull.debug["bull_setup"] is True
    assert bull.exit_side is None
    assert exit_signal.exit_side == "BUY"
    assert exit_signal.debug["exit_signal"] is True


if __name__ == "__main__":
    test_tradeblazer_helpers()
    test_entry_setup_and_trigger()
    test_cancel_entry_after_setup_expires()
    test_exit_uses_previous_bull_setup()
    print("vwm short signal tests ok")
