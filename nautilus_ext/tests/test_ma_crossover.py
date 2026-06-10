"""
MA5 / MA20 moving-average crossover strategy tests.

Coverage
--------
    ma5 value matches reference rolling mean
    ma20 value matches reference rolling mean
    warmup + live path equals all-on-event replay
    BUY signal on upward crossover
    SELL signal on downward crossover
    strategy code uses only public FeatureSnapshot / engine APIs
"""
from __future__ import annotations

import inspect
from dataclasses import dataclass

import pytest

# Strategy-facing imports go through the stable public facade, never the deep
# compute.* paths and never compute/features.py.
from nautilus_ext.features.api import FeatureSpec, SpecFeatureEngine, rolling_mean_spec
from nautilus_ext.features.examples.synthetic_bars import (
    ONE_SECOND_NS,
    BarEvent,
    make_bars,
)
from nautilus_ext.features.runner import FeatureStrategyRunner

# The strategy lives in the top-level, user-facing package; the shared runner
# and explicit registry drive every strategy.
from feature_strategies import output
from feature_strategies.data_loaders import load_events, load_synthetic_bars
from feature_strategies.registry import STRATEGY_REGISTRY, get_entry
from feature_strategies.strategies.ma_crossover import (
    MovingAverageCrossoverConfig,
    MovingAverageCrossoverStrategy,
    build_ma_crossover_specs,
    build_specs,
    crossover_signal,
)


# ---------------------------------------------------------------------------
# Minimal fixtures — same pattern as test_compute_features.py
# ---------------------------------------------------------------------------

@dataclass
class Bar:
    close: float = 0.0
    open: float = 0.0
    high: float = 0.0
    low: float = 0.0
    volume: float = 1.0
    instrument_id: str = "BTC/USDT"
    event_type: str = "bar"
    event_time_ns: int = 0


def _s(n: int) -> int:
    return n * 1_000_000_000


def bars(closes: list[float]) -> list[Bar]:
    return [Bar(close=c, event_time_ns=_s(i)) for i, c in enumerate(closes)]


def _rolling_mean_ref(values: list[float], window: int) -> list[float]:
    return [
        sum(values[i - window + 1: i + 1]) / window
        for i in range(window - 1, len(values))
    ]


def _crossover_signal(
    ma5: float | None,
    ma20: float | None,
    prev_ma5: float | None,
    prev_ma20: float | None,
) -> str:
    if any(v is None for v in (ma5, ma20, prev_ma5, prev_ma20)):
        return "HOLD"
    if prev_ma5 <= prev_ma20 and ma5 > ma20:
        return "BUY"
    if prev_ma5 >= prev_ma20 and ma5 < ma20:
        return "SELL"
    return "HOLD"


# ---------------------------------------------------------------------------
# Engine factory
# ---------------------------------------------------------------------------

def _ma_engine(ma5_window: int = 5, ma20_window: int = 20) -> SpecFeatureEngine:
    specs = [
        FeatureSpec(
            "ma5_close",
            input_type="bar",
            input_field="close",
            window=ma5_window,
            params={"type": "rolling_mean"},
        ),
        FeatureSpec(
            "ma20_close",
            input_type="bar",
            input_field="close",
            window=ma20_window,
            params={"type": "rolling_mean"},
        ),
    ]
    return SpecFeatureEngine(specs=specs, stamp_process_time=False)


# ===========================================================================
# Tests
# ===========================================================================

class TestMACrossover:

    # -----------------------------------------------------------------------
    # Value correctness
    # -----------------------------------------------------------------------

    def test_ma5_value_matches_reference(self):
        closes = [float(i + 1) for i in range(25)]   # 1.0 … 25.0
        engine = _ma_engine()
        snap = None
        for b in bars(closes):
            snap = engine.on_event(b)
        expected = _rolling_mean_ref(closes, 5)[-1]
        assert snap.value("ma5_close") == pytest.approx(expected)

    def test_ma20_value_matches_reference(self):
        closes = [float(i + 1) for i in range(30)]   # 1.0 … 30.0
        engine = _ma_engine()
        snap = None
        for b in bars(closes):
            snap = engine.on_event(b)
        expected = _rolling_mean_ref(closes, 20)[-1]
        assert snap.value("ma20_close") == pytest.approx(expected)

    def test_ma5_not_ready_before_window(self):
        engine = _ma_engine()
        for b in bars([100.0] * 4):
            snap = engine.on_event(b)
        assert not snap.is_ready("ma5_close")

    def test_ma20_not_ready_before_window(self):
        engine = _ma_engine()
        for b in bars([100.0] * 19):
            snap = engine.on_event(b)
        assert not snap.is_ready("ma20_close")

    def test_ma5_ready_at_window(self):
        engine = _ma_engine()
        snap = None
        for b in bars([100.0] * 5):
            snap = engine.on_event(b)
        assert snap.is_ready("ma5_close")
        assert snap.value("ma5_close") == pytest.approx(100.0)

    def test_ma20_ready_at_window(self):
        engine = _ma_engine()
        snap = None
        for b in bars([100.0] * 20):
            snap = engine.on_event(b)
        assert snap.is_ready("ma20_close")
        assert snap.value("ma20_close") == pytest.approx(100.0)

    # -----------------------------------------------------------------------
    # Warmup parity
    # -----------------------------------------------------------------------

    def test_warmup_plus_live_equals_all_on_event(self):
        closes = [float(i + 1) for i in range(30)]
        all_bars = bars(closes)

        # All-on-event path
        engine_a = _ma_engine()
        for b in all_bars:
            engine_a.on_event(b)

        # Warmup-then-live path (split at bar 20)
        engine_b = _ma_engine()
        engine_b.warmup(iter(all_bars[:20]))
        for b in all_bars[20:]:
            engine_b.on_event(b)

        assert engine_a.value("ma5_close")  == pytest.approx(engine_b.value("ma5_close"))
        assert engine_a.value("ma20_close") == pytest.approx(engine_b.value("ma20_close"))

    def test_warmup_advances_watermark(self):
        """Watermark after warmup equals the last warmup event's event_time_ns."""
        engine = _ma_engine()
        warmup_bars = bars([100.0] * 20)
        engine.warmup(iter(warmup_bars))
        # The last warmup bar is at t=19s
        assert engine.watermark_ns >= _s(19)

    # -----------------------------------------------------------------------
    # Crossover signal logic
    # -----------------------------------------------------------------------

    def test_buy_signal_on_upward_crossover(self):
        """MA5 crosses above MA20 when a price spike follows a flat period."""
        # 20 bars at 100 → MA5=MA20=100 (both equal)
        # Bar 21 at 200 → MA5 jumps, MA20 rises slowly → BUY
        closes = [100.0] * 20
        engine = _ma_engine()
        engine.warmup(iter(bars(closes)))

        prev_ma5  = engine.value("ma5_close")    # 100.0
        prev_ma20 = engine.value("ma20_close")   # 100.0

        spike_bar = Bar(close=200.0, event_time_ns=_s(20))
        snap = engine.on_event(spike_bar)

        ma5  = snap.value("ma5_close")
        ma20 = snap.value("ma20_close")

        assert ma5 > ma20, "MA5 should exceed MA20 after spike"
        assert _crossover_signal(ma5, ma20, prev_ma5, prev_ma20) == "BUY"

    def test_sell_signal_on_downward_crossover(self):
        """MA5 crosses below MA20 when a price drop follows a flat period."""
        # 20 bars at 100 → MA5=MA20=100 (both equal)
        # Bar 21 at 0 → MA5 drops hard, MA20 drops slowly → SELL
        closes = [100.0] * 20
        engine = _ma_engine()
        engine.warmup(iter(bars(closes)))

        prev_ma5  = engine.value("ma5_close")    # 100.0
        prev_ma20 = engine.value("ma20_close")   # 100.0

        drop_bar = Bar(close=0.0, event_time_ns=_s(20))
        snap = engine.on_event(drop_bar)

        ma5  = snap.value("ma5_close")
        ma20 = snap.value("ma20_close")

        assert ma5 < ma20, "MA5 should fall below MA20 after price drop"
        assert _crossover_signal(ma5, ma20, prev_ma5, prev_ma20) == "SELL"

    def test_hold_when_no_crossover(self):
        closes = [100.0] * 25
        engine = _ma_engine()
        snaps = [engine.on_event(b) for b in bars(closes)]

        prev_ma5  = snaps[-2].value("ma5_close")
        prev_ma20 = snaps[-2].value("ma20_close")
        ma5  = snaps[-1].value("ma5_close")
        ma20 = snaps[-1].value("ma20_close")

        assert _crossover_signal(ma5, ma20, prev_ma5, prev_ma20) == "HOLD"

    def test_hold_when_not_ready(self):
        assert _crossover_signal(None, None, None, None) == "HOLD"
        assert _crossover_signal(102.0, 100.0, None, 100.0) == "HOLD"

    def test_sequential_crossovers_detected(self):
        """BUY then SELL appear in the expected positions of a live sequence."""
        # Warmup at 100, then spike → hold → drop
        closes = [100.0] * 20
        engine = _ma_engine()
        engine.warmup(iter(bars(closes)))

        live_closes = [110.0] * 3 + [100.0] * 3 + [90.0] * 3 + [80.0] * 3
        signals: list[str] = []
        prev_ma5  = engine.value("ma5_close")
        prev_ma20 = engine.value("ma20_close")

        for i, c in enumerate(live_closes):
            snap = engine.on_event(Bar(close=c, event_time_ns=_s(20 + i)))
            ma5  = snap.value("ma5_close")
            ma20 = snap.value("ma20_close")
            signals.append(_crossover_signal(ma5, ma20, prev_ma5, prev_ma20))
            prev_ma5, prev_ma20 = ma5, ma20

        assert "BUY"  in signals, f"Expected BUY in {signals}"
        assert "SELL" in signals, f"Expected SELL in {signals}"
        # BUY must appear before SELL
        assert signals.index("BUY") < signals.index("SELL")

    # -----------------------------------------------------------------------
    # Public API contract
    # -----------------------------------------------------------------------

    def test_strategy_uses_only_public_api(self):
        """Verify the public API surface: value(), is_ready(), engine.value(), engine.is_ready()."""
        engine = _ma_engine()
        for b in bars([100.0] * 25):
            snap = engine.on_event(b)

        # FeatureSnapshot public API
        assert isinstance(snap.value("ma5_close"), float)
        assert isinstance(snap.value("ma20_close"), float)
        assert snap.is_ready("ma5_close") is True
        assert snap.is_ready("ma20_close") is True
        assert snap.all_ready() is True

        # SpecFeatureEngine public API
        assert isinstance(engine.value("ma5_close"), float)
        assert isinstance(engine.value("ma20_close"), float)
        assert engine.is_ready("ma5_close") is True
        assert engine.is_ready("ma20_close") is True

        # snap.value() and engine.value() agree
        assert snap.value("ma5_close") == pytest.approx(engine.value("ma5_close"))
        assert snap.value("ma20_close") == pytest.approx(engine.value("ma20_close"))

    def test_engine_value_returns_none_before_ready(self):
        engine = _ma_engine()
        for b in bars([100.0] * 4):
            engine.on_event(b)
        assert engine.value("ma5_close") is None
        assert engine.is_ready("ma5_close") is False


# ===========================================================================
# Refactored strategy layer: nautilus_ext.features.strategies.ma_crossover
# ===========================================================================

def _strategy_engine(config: MovingAverageCrossoverConfig) -> SpecFeatureEngine:
    return SpecFeatureEngine(specs=build_ma_crossover_specs(config), stamp_process_time=False)


class _OnlyValueSnapshot:
    """Snapshot stand-in exposing *only* the public ``value`` accessor.

    If MovingAverageCrossoverStrategy reached into snapshot internals (the
    ``.values`` dict, FeatureValue objects, etc.) it would fail against this
    object — so passing it proves the strategy stays on the public API.
    """

    def __init__(self, mapping: dict[str, float | None]) -> None:
        self._mapping = mapping

    def value(self, name: str, default=None):  # noqa: ANN001 - mirror public signature
        return self._mapping.get(name, default)


class TestMACrossoverStrategy:

    def test_build_specs_returns_exactly_two_rolling_mean_specs(self):
        specs = build_ma_crossover_specs(MovingAverageCrossoverConfig())
        assert len(specs) == 2
        assert all(s.params["type"] == "rolling_mean" for s in specs)
        assert {s.name for s in specs} == {"ma5_close", "ma20_close"}
        assert {s.window for s in specs} == {5, 20}

    def test_build_specs_honours_custom_config(self):
        # Names are now derived from windows + input_field.
        config = MovingAverageCrossoverConfig(fast_window=3, slow_window=8)
        windows = {s.name: s.window for s in build_ma_crossover_specs(config)}
        assert windows == {"ma3_close": 3, "ma8_close": 8}

    def test_strategy_emits_buy_on_upward_crossover(self):
        config = MovingAverageCrossoverConfig()
        engine = _strategy_engine(config)
        strategy = MovingAverageCrossoverStrategy(config)
        engine.warmup(iter(bars([100.0] * 20)))

        # First ready snapshot seeds prev (MA5 == MA20 == 100) -> HOLD.
        seed = strategy.on_snapshot(engine.on_event(Bar(close=100.0, event_time_ns=_s(20))))
        spike = strategy.on_snapshot(engine.on_event(Bar(close=200.0, event_time_ns=_s(21))))

        assert seed == "HOLD"
        assert spike == "BUY"

    def test_strategy_emits_sell_on_downward_crossover(self):
        config = MovingAverageCrossoverConfig()
        engine = _strategy_engine(config)
        strategy = MovingAverageCrossoverStrategy(config)
        engine.warmup(iter(bars([100.0] * 20)))

        seed = strategy.on_snapshot(engine.on_event(Bar(close=100.0, event_time_ns=_s(20))))
        drop = strategy.on_snapshot(engine.on_event(Bar(close=0.0, event_time_ns=_s(21))))

        assert seed == "HOLD"
        assert drop == "SELL"

    def test_strategy_emits_hold_when_not_ready(self):
        config = MovingAverageCrossoverConfig()
        engine = _strategy_engine(config)
        strategy = MovingAverageCrossoverStrategy(config)
        # Only 3 bars: neither MA window is filled yet.
        signals = [strategy.on_snapshot(engine.on_event(b)) for b in bars([100.0] * 3)]
        assert signals == ["HOLD", "HOLD", "HOLD"]

    def test_strategy_first_ready_snapshot_is_hold(self):
        """A crossover needs two consecutive ready snapshots; the first is HOLD."""
        config = MovingAverageCrossoverConfig()
        engine = _strategy_engine(config)
        strategy = MovingAverageCrossoverStrategy(config)
        signals = [strategy.on_snapshot(engine.on_event(b)) for b in bars([float(i) for i in range(25)])]
        # The first 4 snapshots are not-ready (HOLD); the 5th is the first ready
        # one and must also be HOLD because there is no prior ready value.
        assert signals[4] == "HOLD"

    def test_strategy_uses_only_snapshot_public_api(self):
        """Strategy works against an object exposing only ``value()``."""
        strategy = MovingAverageCrossoverStrategy(MovingAverageCrossoverConfig())
        assert strategy.on_snapshot(_OnlyValueSnapshot({"ma5_close": 100.0, "ma20_close": 100.0})) == "HOLD"
        assert strategy.on_snapshot(_OnlyValueSnapshot({"ma5_close": 120.0, "ma20_close": 105.0})) == "BUY"
        assert strategy.on_snapshot(_OnlyValueSnapshot({"ma5_close": 90.0, "ma20_close": 105.0})) == "SELL"


class TestSyntheticBars:

    def test_make_bars_shapes_and_spacing(self):
        events = make_bars([100.0, 101.0, 102.0], instrument_id="ETH/USDT")
        assert len(events) == 3
        assert all(isinstance(e, BarEvent) for e in events)
        assert [e.close for e in events] == [100.0, 101.0, 102.0]
        assert all(e.event_type == "bar" for e in events)
        assert all(e.instrument_id == "ETH/USDT" for e in events)
        assert [e.event_time_ns for e in events] == [0, ONE_SECOND_NS, 2 * ONE_SECOND_NS]

    def test_make_bars_feed_engine(self):
        config = MovingAverageCrossoverConfig()
        engine = _strategy_engine(config)
        snap = None
        for bar in make_bars([100.0] * 5):
            snap = engine.on_event(bar)
        assert snap.is_ready("ma5_close")
        assert snap.value("ma5_close") == pytest.approx(100.0)


class TestFeatureStrategyRunner:

    def _runner(self, config: MovingAverageCrossoverConfig) -> FeatureStrategyRunner:
        # The runner builds its own engine from specs (spec-based facade API).
        return FeatureStrategyRunner(
            build_specs(config),
            MovingAverageCrossoverStrategy(config),
        )

    def test_value_and_is_ready_delegate_to_engine(self):
        config = MovingAverageCrossoverConfig()
        runner = self._runner(config)
        assert runner.is_ready(config.fast_name) is False
        runner.warmup(iter(bars([100.0] * 20)))
        assert runner.is_ready(config.fast_name) is True
        assert runner.value(config.fast_name) == pytest.approx(100.0)

    def test_health_summary_delegates_to_engine(self):
        config = MovingAverageCrossoverConfig()
        runner = self._runner(config)
        runner.warmup(iter(bars([100.0] * 20)))
        health = runner.health_summary()
        assert isinstance(health, dict)
        assert health["n_features"] == 2

    def test_on_event_returns_snapshot_and_signal(self):
        config = MovingAverageCrossoverConfig()
        runner = self._runner(config)
        runner.warmup(iter(bars([100.0] * 20)))

        snap0, sig0 = runner.on_event(Bar(close=100.0, event_time_ns=_s(20)))
        snap1, sig1 = runner.on_event(Bar(close=200.0, event_time_ns=_s(21)))

        assert sig0 == "HOLD"
        assert sig1 == "BUY"
        assert snap1.value(config.fast_name) > snap1.value(config.slow_name)

    def test_run_yields_event_snapshot_signal_in_order(self):
        config = MovingAverageCrossoverConfig()
        runner = self._runner(config)
        runner.warmup(iter(bars([100.0] * 20)))

        live = make_bars([100.0, 110.0, 110.0], start_ns=_s(20))
        rows = list(runner.run(live))

        assert [event for event, _, _ in rows] == live
        signals = [signal for _, _, signal in rows]
        assert signals[0] == "HOLD"      # seed
        assert "BUY" in signals
        # snapshot/signal agree with strategy semantics
        for _, snap, _ in rows:
            assert snap is not None

    def test_runner_matches_direct_calls(self):
        """Runner output equals driving engine + strategy by hand."""
        config = MovingAverageCrossoverConfig()
        live_closes = [100.0] + [110.0] * 3 + [90.0] * 3

        # via runner
        runner = self._runner(config)
        runner.warmup(iter(bars([100.0] * 20)))
        runner_signals = [s for _, _, s in runner.run(make_bars(live_closes, start_ns=_s(20)))]

        # by hand
        engine = _strategy_engine(config)
        strategy = MovingAverageCrossoverStrategy(config)
        engine.warmup(iter(bars([100.0] * 20)))
        manual_signals = [
            strategy.on_snapshot(engine.on_event(b))
            for b in make_bars(live_closes, start_ns=_s(20))
        ]

        assert runner_signals == manual_signals


def _config_path() -> "Path":
    from pathlib import Path

    return Path(__file__).resolve().parents[2] / "feature_strategies" / "configs" / "ma_crossover.yaml"


class TestConfigAndPureSignal:
    """Compact strategy file: property-derived names + pure crossover function."""

    def test_default_config_generates_names(self):
        config = MovingAverageCrossoverConfig()
        assert config.fast_name == "ma5_close"
        assert config.slow_name == "ma20_close"

    def test_custom_config_generates_names(self):
        config = MovingAverageCrossoverConfig(fast_window=3, slow_window=8, input_field="open")
        assert config.fast_name == "ma3_open"
        assert config.slow_name == "ma8_open"

    def test_build_specs_names_and_windows(self):
        config = MovingAverageCrossoverConfig(fast_window=3, slow_window=8)
        specs = build_specs(config)
        assert [s.name for s in specs] == ["ma3_close", "ma8_close"]
        assert [s.window for s in specs] == [3, 8]
        assert all(s.params["type"] == "rolling_mean" for s in specs)

    def test_crossover_signal_hold_when_any_none(self):
        assert crossover_signal(None, None, None, None) == "HOLD"
        assert crossover_signal(100.0, 100.0, 102.0, None) == "HOLD"
        assert crossover_signal(None, 100.0, 102.0, 100.0) == "HOLD"

    def test_crossover_signal_buy(self):
        # prev fast <= slow, now fast > slow
        assert crossover_signal(100.0, 100.0, 120.0, 105.0) == "BUY"

    def test_crossover_signal_sell(self):
        # prev fast >= slow, now fast < slow
        assert crossover_signal(100.0, 100.0, 80.0, 95.0) == "SELL"

    def test_crossover_signal_hold_when_no_cross(self):
        # fast stays above slow on both sides — no crossover
        assert crossover_signal(120.0, 100.0, 130.0, 105.0) == "HOLD"


class TestSpecBuilder:
    """The public rolling_mean_spec builder and its use in the strategy."""

    def test_rolling_mean_spec_sets_params_type(self):
        spec = rolling_mean_spec("ma10_close", input_type="bar", input_field="close", window=10)
        assert isinstance(spec, FeatureSpec)
        assert spec.name == "ma10_close"
        assert spec.window == 10
        assert spec.input_type == "bar"
        assert spec.input_field == "close"
        assert spec.params == {"type": "rolling_mean"}

    def test_strategy_uses_rolling_mean_spec_not_raw_params(self):
        """The strategy declares features via the builder, not raw params={...}."""
        import feature_strategies.strategies.ma_crossover as strat

        src = inspect.getsource(strat)
        assert "rolling_mean_spec" in src
        assert 'params={"type"' not in src  # no hand-written params plumbing

    def test_build_specs_still_two_rolling_mean_specs(self):
        specs = build_specs(MovingAverageCrossoverConfig())
        assert len(specs) == 2
        assert all(s.params["type"] == "rolling_mean" for s in specs)


class TestDataLoaders:
    """Data source selection lives outside run_strategy.py."""

    def test_load_synthetic_bars_returns_warmup_and_live(self):
        warmup, live = load_synthetic_bars({"warmup_bars": 20, "live_bars": 12})
        assert len(warmup) == 20
        assert len(live) == 12
        assert all(isinstance(b, BarEvent) for b in warmup + live)
        # Live bars continue in time after warmup.
        assert live[0].event_time_ns == len(warmup) * ONE_SECOND_NS

    def test_load_events_synthetic_mode(self):
        warmup, live = load_events({"mode": "synthetic", "warmup_bars": 5, "live_bars": 5})
        assert len(warmup) == 5 and len(live) == 5

    def test_load_events_defaults_to_synthetic(self):
        warmup, live = load_events({})
        assert warmup and live

    def test_load_events_honours_instrument_id(self):
        warmup, _ = load_synthetic_bars({"instrument_id": "ETH/USDT", "warmup_bars": 3, "live_bars": 3})
        assert all(b.instrument_id == "ETH/USDT" for b in warmup)

    def test_load_events_unsupported_mode_raises(self):
        with pytest.raises(ValueError, match="unsupported data mode"):
            load_events({"mode": "live_feed"})


class _BareEvent:
    """An event lacking close / event_time_ns — output must not crash."""


class TestOutput:
    """Display formatting is defensive about event shape."""

    def _snapshot_stub(self, values):
        class _Snap:
            def value(self, name, default=None):
                return values.get(name, default)

        return _Snap()

    def test_row_with_close_and_time(self, capsys):
        bar = make_bars([100.0], start_ns=5 * ONE_SECOND_NS)[0]
        snap = self._snapshot_stub({"ma5_close": 100.0})
        output.print_event_row(bar, snap, "BUY", ["ma5_close"])
        out = capsys.readouterr().out
        assert "BUY" in out
        assert "100.00" in out          # close rendered
        assert "5" in out               # t(s) rendered

    def test_row_without_close_or_time(self, capsys):
        snap = self._snapshot_stub({"ma5_close": None})
        output.print_event_row(_BareEvent(), snap, "HOLD", ["ma5_close"])
        out = capsys.readouterr().out
        assert "HOLD" in out
        assert "-" in out               # missing close/time rendered as "-"

    def test_warmup_summary_and_header(self, capsys):
        config = MovingAverageCrossoverConfig()
        runner = FeatureStrategyRunner(build_specs(config), MovingAverageCrossoverStrategy(config))
        runner.warmup(iter(bars([100.0] * 20)))
        output.print_warmup_summary("ma_crossover", 20, runner, [config.fast_name, config.slow_name])
        output.print_event_table_header([config.fast_name, config.slow_name])
        out = capsys.readouterr().out
        assert "[ma_crossover] warmed up on 20 bars" in out
        assert "signal" in out


class TestRegistry:
    """The explicit strategy registry."""

    def test_registry_contains_ma_crossover(self):
        assert "ma_crossover" in STRATEGY_REGISTRY

    def test_entry_wires_config_strategy_and_build_specs(self):
        entry = get_entry("ma_crossover")
        assert entry.config_cls is MovingAverageCrossoverConfig
        assert entry.strategy_cls is MovingAverageCrossoverStrategy
        assert entry.build_specs is build_specs

    def test_unknown_strategy_raises_helpful_error(self):
        with pytest.raises(KeyError, match="Unknown strategy"):
            get_entry("does_not_exist")


class TestTopLevelLayer:
    """The user-facing layer: top-level package, facades, shared runner."""

    def test_strategy_imports_cleanly_from_top_level(self):
        import feature_strategies.strategies.ma_crossover as strat

        assert hasattr(strat, "MovingAverageCrossoverConfig")
        assert hasattr(strat, "MovingAverageCrossoverStrategy")
        assert hasattr(strat, "build_specs")
        # build_specs is the canonical name; the old name remains as an alias.
        assert strat.build_ma_crossover_specs is strat.build_specs

    def test_strategy_imports_only_public_api(self):
        """The strategy module must not reach into compute internals."""
        import feature_strategies.strategies.ma_crossover as strat

        src = inspect.getsource(strat)
        assert "features.compute" not in src
        assert "import features" not in src
        assert "nautilus_ext.features.api" in src

    def test_build_specs_returns_two_rolling_mean_specs(self):
        specs = build_specs(MovingAverageCrossoverConfig())
        assert len(specs) == 2
        assert all(isinstance(s, FeatureSpec) for s in specs)
        assert all(s.params["type"] == "rolling_mean" for s in specs)

    def test_run_strategy_with_config(self, capsys):
        from feature_strategies.run_strategy import main

        main(["--config", str(_config_path())])
        out = capsys.readouterr().out
        assert "[ma_crossover] warmed up" in out
        assert "BUY" in out
        assert "SELL" in out

    def test_run_strategy_with_strategy_flag_only(self, capsys):
        from feature_strategies.run_strategy import main

        main(["--strategy", "ma_crossover"])
        out = capsys.readouterr().out
        assert "[ma_crossover] warmed up" in out
        assert "BUY" in out

    def test_run_strategy_requires_a_strategy(self):
        from feature_strategies.run_strategy import main

        with pytest.raises(SystemExit):
            main([])  # no --config and no --strategy

    def test_legacy_wrapper_still_works(self, capsys):
        import scripts.run_ma_crossover_demo as legacy
        from feature_strategies.run_strategy import main as shared_main

        # The wrapper forwards to the shared runner.
        assert legacy.main is shared_main
        legacy.main(["--config", str(_config_path())])
        out = capsys.readouterr().out
        assert "[ma_crossover] warmed up" in out
        assert "BUY" in out
        assert "SELL" in out
