"""Trade (tick) features (pure Python).

Consume ``TradeEvent`` (input_type ``trade``).  Two windowing styles are used:

* **count-window** (last N trades) — volume sums, average size, signed volume,
  imbalance, VWAP, large-trade ratio.
* **time-window** (last ``window`` ``window_unit``) — trade count and trade
  intensity (trades per second), via ``TimeWindowState``.

    TradeCountFeature          — number of trades in the time window
    TradePriceMeanFeature      — arithmetic mean(price) in the time window
    TradeVolumeSumFeature      — rolling sum(quantity)
    TradeQuoteVolumeSumFeature — rolling sum(quote_quantity)
    AvgTradeSizeFeature        — rolling mean(quantity)
    SignedTradeVolumeFeature   — rolling sum(+qty for BUY, -qty for SELL)
    TradeImbalanceFeature      — (buy_vol - sell_vol) / max(buy_vol+sell_vol, eps)
    TradeVWAPFeature           — sum(price*qty) / sum(qty)
    LargeTradeRatioFeature     — fraction of trades with qty >= threshold
    TradeIntensityFeature      — trade_count / window_seconds

No ``nautilus_trader`` import; all maths is plain Python.
"""
from __future__ import annotations

from typing import Any

from feature_engine.compute.feature_lib.base import (
    _EPS,
    _NS_PER_UNIT,
    _AbstractFeature,
    _bar_field,
    _ts_ns,
    FeatureUpdate,
    FeatureValue,
    RollingWindowState,
    VWAPState,
    WarmupRequirement,
)
from feature_engine.compute.spec import FeatureSpec
from feature_engine.compute.state import TimeWindowState

_BUY, _SELL = "BUY", "SELL"


def _restore_cached(feature: _AbstractFeature, value: float | None, ready: bool) -> None:
    """Rebuild ``feature._cached`` after a state restore.

    ``_load_base`` resets the cache to ``value=None, is_ready=False``; the legacy
    OHLCV features rebuild it from the restored state so that, after a warm
    restart, ``feature.value`` stays consistent with ``feature.is_ready``.  Trade
    features must do the same.  When not ready we leave the not-ready default
    that ``_load_base`` already installed.
    """
    if ready:
        feature._cached = FeatureValue(name=feature._spec.name, value=value, is_ready=True)


def _trade_side(event: Any) -> str | None:
    """Aggressor side from ``side``, falling back to ``is_buyer_maker``."""
    side = getattr(event, "side", None)
    if side is not None:
        return str(side)
    ibm = getattr(event, "is_buyer_maker", None)
    if ibm is None:
        return None
    return _SELL if ibm else _BUY


def _window_ns(spec: FeatureSpec) -> int:
    """Resolve a time window (window + window_unit) to nanoseconds."""
    unit = spec.window_unit or "seconds"
    if unit not in _NS_PER_UNIT:
        raise ValueError(
            f"FeatureSpec {spec.name!r}: time-window feature needs window_unit in "
            f"{sorted(_NS_PER_UNIT)}, got {unit!r}."
        )
    return int((spec.window or 1) * _NS_PER_UNIT[unit])


# ---------------------------------------------------------------------------
# Time-window features
# ---------------------------------------------------------------------------

class TradeCountFeature(_AbstractFeature):
    """Number of trades within the trailing time window (``window`` ``window_unit``)."""

    def __init__(self, spec: FeatureSpec) -> None:
        super().__init__(spec)
        self._state = TimeWindowState(window_ns=_window_ns(spec))

    def warmup_required(self) -> WarmupRequirement:
        return WarmupRequirement(n_events=1, unit="events", mandatory=False)

    @property
    def is_ready(self) -> bool:
        return self._cached.is_ready

    def reset(self) -> None:
        self._state.reset()
        self._reset_base()

    def update(self, event: Any) -> FeatureUpdate:
        self._event_count += 1
        ts_ns = _ts_ns(event, self._spec.trigger.time_semantics)
        self._state.push(ts_ns, 1.0)
        triggered = self._should_trigger(ts_ns)
        if triggered:
            self._last_trigger_ts = ts_ns
        return self._emit(float(self._state.count), True, triggered,
                          source_event_time_ns=ts_ns, update_status="updated")

    def state_dict(self) -> dict:
        return {**self._base_state(), "tw": self._state.state_dict()}

    def load_state_dict(self, state: dict) -> None:
        self._load_base(state)
        self._state.load_state_dict(state["tw"])
        # update() always emits ready=True; restored-ready iff it had any event.
        ready = self._event_count > 0
        _restore_cached(self, float(self._state.count) if ready else None, ready)


class TradeIntensityFeature(_AbstractFeature):
    """Trades per second over the trailing time window: ``count / window_seconds``."""

    def __init__(self, spec: FeatureSpec) -> None:
        super().__init__(spec)
        wns = _window_ns(spec)
        self._state = TimeWindowState(window_ns=wns)
        self._window_seconds = wns / 1_000_000_000

    def warmup_required(self) -> WarmupRequirement:
        return WarmupRequirement(n_events=1, unit="events", mandatory=False)

    @property
    def is_ready(self) -> bool:
        return self._cached.is_ready

    def reset(self) -> None:
        self._state.reset()
        self._reset_base()

    def update(self, event: Any) -> FeatureUpdate:
        self._event_count += 1
        ts_ns = _ts_ns(event, self._spec.trigger.time_semantics)
        self._state.push(ts_ns, 1.0)
        triggered = self._should_trigger(ts_ns)
        if triggered:
            self._last_trigger_ts = ts_ns
        intensity = self._state.count / max(self._window_seconds, _EPS)
        return self._emit(intensity, True, triggered,
                          source_event_time_ns=ts_ns, update_status="updated")

    def state_dict(self) -> dict:
        return {**self._base_state(), "tw": self._state.state_dict()}

    def load_state_dict(self, state: dict) -> None:
        self._load_base(state)
        self._state.load_state_dict(state["tw"])
        # update() always emits ready=True; restored-ready iff it had any event.
        ready = self._event_count > 0
        intensity = self._state.count / max(self._window_seconds, _EPS)
        _restore_cached(self, intensity if ready else None, ready)


class TradePriceMeanFeature(_AbstractFeature):
    """Arithmetic mean of trade prices in the trailing event-time window.

    The window is expressed by ``FeatureSpec.window`` + ``window_unit`` and is
    maintained by :class:`TimeWindowState`, so it follows actual trade
    timestamps rather than a trade count or a synthetic bar clock.  Readiness
    is withheld until one complete duration has elapsed from the first event;
    this prevents the first partial 5/10-minute window from producing a signal.
    """

    def __init__(self, spec: FeatureSpec) -> None:
        super().__init__(spec)
        self._state = TimeWindowState(window_ns=_window_ns(spec))
        self._first_ts_ns: int | None = None

    def warmup_required(self) -> WarmupRequirement:
        return WarmupRequirement(
            n_events=self._state.window_ns,
            unit="nanoseconds",
            mandatory=True,
        )

    @property
    def is_ready(self) -> bool:
        newest = self._state.newest_ts_ns
        return (
            self._first_ts_ns is not None
            and newest is not None
            and newest - self._first_ts_ns >= self._state.window_ns
        )

    def reset(self) -> None:
        self._state.reset()
        self._first_ts_ns = None
        self._reset_base()

    def update(self, event: Any) -> FeatureUpdate:
        self._event_count += 1
        ts_ns = _ts_ns(event, self._spec.trigger.time_semantics)
        price = _bar_field(event, self._spec.input_field or "price")
        if price is None:
            return self._missing_field(self._spec.input_field or "price")
        if self._first_ts_ns is None:
            self._first_ts_ns = ts_ns
        self._state.push(ts_ns, price)
        triggered = self._should_trigger(ts_ns)
        if triggered:
            self._last_trigger_ts = ts_ns
        ready = self.is_ready
        return self._emit(
            self._state.mean if ready else None,
            ready,
            triggered,
            window_start_ns=ts_ns - self._state.window_ns,
            window_end_ns=ts_ns,
            source_event_time_ns=ts_ns,
            update_status="updated" if ready else "not_ready",
        )

    def state_dict(self) -> dict:
        return {
            **self._base_state(),
            "tw": self._state.state_dict(),
            "first_ts_ns": self._first_ts_ns,
        }

    def load_state_dict(self, state: dict) -> None:
        self._load_base(state)
        self._state.load_state_dict(state["tw"])
        self._first_ts_ns = state.get("first_ts_ns")
        ready = self.is_ready
        _restore_cached(self, self._state.mean if ready else None, ready)


# ---------------------------------------------------------------------------
# Count-window aggregates (last N trades)
# ---------------------------------------------------------------------------

class TradeVolumeSumFeature(_AbstractFeature):
    """Rolling sum of ``quantity`` over the last N trades."""

    _FIELD = "quantity"

    def __init__(self, spec: FeatureSpec) -> None:
        super().__init__(spec)
        self._state = RollingWindowState(maxlen=spec.window or 1)
        self._field = spec.input_field or self._FIELD

    def warmup_required(self) -> WarmupRequirement:
        return WarmupRequirement(n_events=self._spec.window or 1, unit="events")

    @property
    def is_ready(self) -> bool:
        return self._state.is_full

    def reset(self) -> None:
        self._state.reset()
        self._reset_base()

    def _value(self) -> float:
        return self._state.sum

    def update(self, event: Any) -> FeatureUpdate:
        self._event_count += 1
        ts_ns = _ts_ns(event, self._spec.trigger.time_semantics)
        v = _bar_field(event, self._field)
        if v is None:
            return self._missing_field(self._field)
        self._state.push(v)
        triggered = self._should_trigger(ts_ns)
        if triggered:
            self._last_trigger_ts = ts_ns
        ready = self._state.is_full
        return self._emit(self._value() if ready else None, ready, triggered,
                          source_event_time_ns=ts_ns,
                          update_status="updated" if ready else "not_ready")

    def state_dict(self) -> dict:
        return {**self._base_state(), "rolling": self._state.state_dict()}

    def load_state_dict(self, state: dict) -> None:
        self._load_base(state)
        self._state.load_state_dict(state["rolling"])
        ready = self._state.is_full
        # self._value() is overridden by AvgTradeSizeFeature (mean vs sum).
        _restore_cached(self, self._value() if ready else None, ready)


class AvgTradeSizeFeature(TradeVolumeSumFeature):
    """Rolling mean of ``quantity`` over the last N trades."""

    def _value(self) -> float:
        return self._state.mean or 0.0


class TradeQuoteVolumeSumFeature(_AbstractFeature):
    """Rolling sum of ``quote_quantity`` (falls back to ``price*quantity``)."""

    def __init__(self, spec: FeatureSpec) -> None:
        super().__init__(spec)
        self._state = RollingWindowState(maxlen=spec.window or 1)

    def warmup_required(self) -> WarmupRequirement:
        return WarmupRequirement(n_events=self._spec.window or 1, unit="events")

    @property
    def is_ready(self) -> bool:
        return self._state.is_full

    def reset(self) -> None:
        self._state.reset()
        self._reset_base()

    def update(self, event: Any) -> FeatureUpdate:
        self._event_count += 1
        ts_ns = _ts_ns(event, self._spec.trigger.time_semantics)
        qv = _bar_field(event, "quote_quantity")
        if qv is None:
            price = _bar_field(event, "price")
            qty = _bar_field(event, "quantity")
            if price is None or qty is None:
                return self._no_change()
            qv = price * qty
        self._state.push(qv)
        triggered = self._should_trigger(ts_ns)
        if triggered:
            self._last_trigger_ts = ts_ns
        ready = self._state.is_full
        return self._emit(self._state.sum if ready else None, ready, triggered,
                          source_event_time_ns=ts_ns,
                          update_status="updated" if ready else "not_ready")

    def state_dict(self) -> dict:
        return {**self._base_state(), "rolling": self._state.state_dict()}

    def load_state_dict(self, state: dict) -> None:
        self._load_base(state)
        self._state.load_state_dict(state["rolling"])
        ready = self._state.is_full
        _restore_cached(self, self._state.sum if ready else None, ready)


class SignedTradeVolumeFeature(_AbstractFeature):
    """Rolling sum of signed quantity (+qty for BUY, -qty for SELL) over N trades."""

    def __init__(self, spec: FeatureSpec) -> None:
        super().__init__(spec)
        self._state = RollingWindowState(maxlen=spec.window or 1)

    def warmup_required(self) -> WarmupRequirement:
        return WarmupRequirement(n_events=self._spec.window or 1, unit="events")

    @property
    def is_ready(self) -> bool:
        return self._state.is_full

    def reset(self) -> None:
        self._state.reset()
        self._reset_base()

    def update(self, event: Any) -> FeatureUpdate:
        self._event_count += 1
        ts_ns = _ts_ns(event, self._spec.trigger.time_semantics)
        qty = _bar_field(event, "quantity")
        side = _trade_side(event)
        if qty is None or side is None:
            return self._no_change()
        signed = qty if side == _BUY else -qty
        self._state.push(signed)
        triggered = self._should_trigger(ts_ns)
        if triggered:
            self._last_trigger_ts = ts_ns
        ready = self._state.is_full
        return self._emit(self._state.sum if ready else None, ready, triggered,
                          source_event_time_ns=ts_ns,
                          update_status="updated" if ready else "not_ready")

    def state_dict(self) -> dict:
        return {**self._base_state(), "rolling": self._state.state_dict()}

    def load_state_dict(self, state: dict) -> None:
        self._load_base(state)
        self._state.load_state_dict(state["rolling"])
        ready = self._state.is_full
        _restore_cached(self, self._state.sum if ready else None, ready)


class TradeImbalanceFeature(_AbstractFeature):
    """Order-flow imbalance over the last N trades::

        (buy_volume - sell_volume) / max(buy_volume + sell_volume, eps)
    """

    def __init__(self, spec: FeatureSpec) -> None:
        super().__init__(spec)
        n = spec.window or 1
        self._buy = RollingWindowState(maxlen=n)
        self._sell = RollingWindowState(maxlen=n)

    def warmup_required(self) -> WarmupRequirement:
        return WarmupRequirement(n_events=self._spec.window or 1, unit="events")

    @property
    def is_ready(self) -> bool:
        return self._buy.is_full and self._sell.is_full

    def reset(self) -> None:
        self._buy.reset()
        self._sell.reset()
        self._reset_base()

    def update(self, event: Any) -> FeatureUpdate:
        self._event_count += 1
        ts_ns = _ts_ns(event, self._spec.trigger.time_semantics)
        qty = _bar_field(event, "quantity")
        side = _trade_side(event)
        if qty is None or side is None:
            return self._no_change()
        self._buy.push(qty if side == _BUY else 0.0)
        self._sell.push(qty if side == _SELL else 0.0)
        triggered = self._should_trigger(ts_ns)
        if triggered:
            self._last_trigger_ts = ts_ns
        if not self.is_ready:
            return self._emit(None, False, triggered,
                              source_event_time_ns=ts_ns, update_status="not_ready")
        buy_vol, sell_vol = self._buy.sum, self._sell.sum
        imb = (buy_vol - sell_vol) / max(buy_vol + sell_vol, _EPS)
        return self._emit(imb, True, triggered,
                          source_event_time_ns=ts_ns, update_status="updated")

    def state_dict(self) -> dict:
        return {**self._base_state(), "buy": self._buy.state_dict(), "sell": self._sell.state_dict()}

    def load_state_dict(self, state: dict) -> None:
        self._load_base(state)
        self._buy.load_state_dict(state["buy"])
        self._sell.load_state_dict(state["sell"])
        ready = self.is_ready
        if ready:
            buy_vol, sell_vol = self._buy.sum, self._sell.sum
            imb = (buy_vol - sell_vol) / max(buy_vol + sell_vol, _EPS)
            _restore_cached(self, imb, True)


class TradeVWAPFeature(_AbstractFeature):
    """Trade VWAP over the last N trades: ``sum(price*qty) / sum(qty)``."""

    def __init__(self, spec: FeatureSpec) -> None:
        super().__init__(spec)
        self._n = spec.window or 1
        self._state = VWAPState(window=self._n)

    def warmup_required(self) -> WarmupRequirement:
        return WarmupRequirement(n_events=self._n, unit="events")

    @property
    def is_ready(self) -> bool:
        return self._state.count >= self._n

    def reset(self) -> None:
        self._state.reset()
        self._reset_base()

    def update(self, event: Any) -> FeatureUpdate:
        self._event_count += 1
        ts_ns = _ts_ns(event, self._spec.trigger.time_semantics)
        price = _bar_field(event, "price")
        qty = _bar_field(event, "quantity")
        if price is None or qty is None:
            return self._no_change()
        self._state.push(price, qty, ts_ns=ts_ns)
        triggered = self._should_trigger(ts_ns)
        if triggered:
            self._last_trigger_ts = ts_ns
        ready = self._state.count >= self._n and self._state.vwap is not None
        return self._emit(self._state.vwap if ready else None, ready, triggered,
                          source_event_time_ns=ts_ns,
                          update_status="updated" if ready else "not_ready")

    def state_dict(self) -> dict:
        return {**self._base_state(), "vwap": self._state.state_dict()}

    def load_state_dict(self, state: dict) -> None:
        self._load_base(state)
        self._state.load_state_dict(state["vwap"])
        ready = self._state.count >= self._n and self._state.vwap is not None
        _restore_cached(self, self._state.vwap if ready else None, ready)


class LargeTradeRatioFeature(_AbstractFeature):
    """Fraction of the last N trades with ``quantity >= threshold``.

    Parameters (from ``params``)
    -----------------------------
    threshold : float   — minimum quantity to count as a "large" trade (required).
    """

    def __init__(self, spec: FeatureSpec) -> None:
        super().__init__(spec)
        threshold = spec.params.get("threshold")
        if threshold is None:
            raise ValueError(
                f"LargeTradeRatioFeature {spec.name!r}: params['threshold'] is required "
                f"(minimum quantity for a 'large' trade)."
            )
        self._threshold = float(threshold)
        self._state = RollingWindowState(maxlen=spec.window or 1)

    def warmup_required(self) -> WarmupRequirement:
        return WarmupRequirement(n_events=self._spec.window or 1, unit="events")

    @property
    def is_ready(self) -> bool:
        return self._state.is_full

    def reset(self) -> None:
        self._state.reset()
        self._reset_base()

    def update(self, event: Any) -> FeatureUpdate:
        self._event_count += 1
        ts_ns = _ts_ns(event, self._spec.trigger.time_semantics)
        qty = _bar_field(event, "quantity")
        if qty is None:
            return self._missing_field("quantity")
        self._state.push(1.0 if qty >= self._threshold else 0.0)
        triggered = self._should_trigger(ts_ns)
        if triggered:
            self._last_trigger_ts = ts_ns
        if not self._state.is_full:
            return self._emit(None, False, triggered,
                              source_event_time_ns=ts_ns, update_status="not_ready")
        ratio = self._state.sum / max(float(self._state.count), _EPS)
        return self._emit(ratio, True, triggered,
                          source_event_time_ns=ts_ns, update_status="updated")

    def state_dict(self) -> dict:
        return {**self._base_state(), "rolling": self._state.state_dict()}

    def load_state_dict(self, state: dict) -> None:
        self._load_base(state)
        self._state.load_state_dict(state["rolling"])
        ready = self._state.is_full
        ratio = self._state.sum / max(float(self._state.count), _EPS)
        _restore_cached(self, ratio if ready else None, ready)
