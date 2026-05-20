"""Smoke tests for the pure VWM short signal engine.

Run with:
    python internal_examples/test_vwm_short_signal.py
"""

from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from nautilus_ext.strategies.vwm_short_signals import VwmShortBarInput
from nautilus_ext.strategies.vwm_short_signals import VwmShortSignalConfig
from nautilus_ext.strategies.vwm_short_signals import VolumeWeightedMomentumShortSignalEngine


def bar(open_, high, low, close, volume=1.0):
    return VwmShortBarInput(
        open=float(open_),
        high=float(high),
        low=float(low),
        close=float(close),
        volume=float(volume),
    )


def new_engine(setup_len=2):
    return VolumeWeightedMomentumShortSignalEngine(
        VwmShortSignalConfig(
            mom_len=1,
            avg_len=2,
            atr_len=1,
            atr_pcnt=0.5,
            setup_len=setup_len,
        ),
    )


def seed_bear_setup(engine):
    engine.update(bar(10, 11, 9, 10))
    engine.update(bar(12, 13, 11, 12))
    return engine.update(bar(9, 13, 9, 9))


def test_bear_setup_records_se_price_and_resets_s_setup():
    engine = new_engine()
    result = seed_bear_setup(engine)

    assert result.bear_setup is True
    assert result.se_price == 9.0
    assert result.s_setup == 0


def test_setup_increments_and_triggers_entry_within_setup_len():
    engine = new_engine(setup_len=2)
    seed_bear_setup(engine)

    result = engine.update(bar(7, 8, 6, 7))

    assert result.s_setup == 1
    assert result.entry_trigger_price == 7.0
    assert result.entry_signal is True
    assert engine.position == -1


def test_entry_expires_after_setup_len():
    engine = new_engine(setup_len=1)
    seed_bear_setup(engine)

    result_1 = engine.update(bar(12, 13, 7.5, 12))
    result_2 = engine.update(bar(12, 13, 7.0, 12))
    result_3 = engine.update(bar(12, 13, 6, 12))

    assert result_1.entry_signal is False
    assert result_2.s_setup == 2
    assert result_2.entry_signal is False
    assert result_3.s_setup == 3
    assert result_3.entry_signal is False
    assert result_3.cancel_entry is True


def test_bull_setup_exits_existing_short_on_next_bar():
    engine = new_engine(setup_len=2)
    seed_bear_setup(engine)
    entry = engine.update(bar(7, 8, 6, 7))
    bull = engine.update(bar(20, 21, 19, 20))
    exit_result = engine.update(bar(20, 21, 19, 20))

    assert entry.entry_signal is True
    assert bull.bull_setup is True
    assert bull.exit_signal is False
    assert exit_result.exit_signal is True
    assert engine.position == 0


def test_zero_volume_does_not_enter_or_exit():
    engine = new_engine(setup_len=2)
    seed_bear_setup(engine)
    no_entry = engine.update(bar(7, 8, 6, 7, volume=0))

    engine.position = -1
    engine.bars_since_entry = 1
    engine.prev_bull_setup = True
    no_exit = engine.update(bar(7, 8, 6, 7, volume=0))

    assert no_entry.entry_signal is False
    assert no_exit.exit_signal is False


def main():
    test_bear_setup_records_se_price_and_resets_s_setup()
    test_setup_increments_and_triggers_entry_within_setup_len()
    test_entry_expires_after_setup_len()
    test_bull_setup_exits_existing_short_on_next_bar()
    test_zero_volume_does_not_enter_or_exit()
    print("vwm short signal smoke tests ok")


if __name__ == "__main__":
    main()
