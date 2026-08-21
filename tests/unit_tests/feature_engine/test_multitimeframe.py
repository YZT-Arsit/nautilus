import pytest

from data_engine.events import BarEvent
from feature_engine.api import rolling_mean_spec
from feature_engine.multitimeframe import MultiTimeframeConfluence, MultiTimeframeFeatureStrategyRunner


class Capture:
    def on_snapshot(self, snapshot):
        return snapshot.value("15m.close")


def bar(value: float, ts: int) -> BarEvent:
    return BarEvent(close=value, open=value, high=value, low=value, volume=1.0,
                    instrument_id="BTCUSDT-PERP.BINANCE", event_time_ns=ts)


def test_higher_timeframe_is_visible_only_after_its_completed_bar_time() -> None:
    runner = MultiTimeframeFeatureStrategyRunner(
        {"1m": [rolling_mean_spec("close", window=1)],
         "15m": [rolling_mean_spec("close", window=1)]},
        Capture(),
    )
    runner.on_completed_bar("1m", bar(101.0, 60), completed_at_ns=60)
    runner.on_completed_bar("15m", bar(100.0, 0), completed_at_ns=900)
    with pytest.raises(LookupError):
        runner.decide(899)
    snapshot, signal = runner.decide(900)
    assert snapshot.value("15m.close") == 100.0
    assert signal == 100.0


def test_completed_bar_publication_must_be_monotonic_per_timeframe() -> None:
    runner = MultiTimeframeFeatureStrategyRunner(
        {"5m": [rolling_mean_spec("close", window=1)]}, Capture()
    )
    runner.on_completed_bar("5m", bar(1.0, 0), completed_at_ns=300)
    with pytest.raises(ValueError):
        runner.on_completed_bar("5m", bar(2.0, 0), completed_at_ns=299)


def test_mtf_state_persists_but_trigger_requires_same_completion_time() -> None:
    confluence = MultiTimeframeConfluence(("5m", "15m"))
    confluence.publish("15m", completed_at_ns=900, state=True, triggered=True)
    confluence.publish("5m", completed_at_ns=1200, state=True, triggered=True)
    assert confluence.state_confluence()
    assert not confluence.trigger_confluence(completed_at_ns=1200)
    confluence.publish("15m", completed_at_ns=1800, state=False)
    assert not confluence.state_confluence()
    confluence.publish("15m", completed_at_ns=1800, state=True, triggered=True)
    confluence.publish("5m", completed_at_ns=1800, state=True, triggered=True)
    assert confluence.trigger_confluence(completed_at_ns=1800)
    with pytest.raises(ValueError):
        confluence.publish("15m", completed_at_ns=1799, state=True)
