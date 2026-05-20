"""Lightweight VWM logic smoke test used by StrategyTemplate.

This avoids starting Nautilus BacktestEngine and only checks the state engine
that ``internal_examples.strategy_template.StrategyTemplate`` uses internally.
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


def main():
    engine = VolumeWeightedMomentumShortSignalEngine(
        VwmShortSignalConfig(
            mom_len=1,
            avg_len=2,
            atr_len=1,
            atr_pcnt=0.5,
            setup_len=2,
        ),
    )
    engine.update(bar(10, 11, 9, 10))
    engine.update(bar(12, 13, 11, 12))
    bear = engine.update(bar(9, 13, 9, 9))
    entry = engine.update(bar(7, 8, 6, 7))
    bull = engine.update(bar(20, 21, 19, 20))
    exit_signal = engine.update(bar(20, 21, 19, 20))

    assert bear.bear_setup is True
    assert bear.se_price == 9.0
    assert bear.s_setup == 0
    assert entry.entry_signal is True
    assert bull.bull_setup is True
    assert exit_signal.exit_signal is True
    print("strategy template VWM smoke ok")


if __name__ == "__main__":
    main()
