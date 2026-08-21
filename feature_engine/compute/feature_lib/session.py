"""UTC crypto-session and completed-timeframe feature operators.

These operators implement the project research calendar, not an exchange open
or close.  Bar timestamps are decision/close timestamps; an observation ending
exactly at midnight belongs to the preceding half-open UTC session.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Any

from feature_engine.compute.features import _AbstractFeature, _field, _ts_ns
from feature_engine.compute.spec import FeatureSpec, FeatureUpdate, WarmupRequirement


DAY_NS = 86_400_000_000_000


def utc_session_start_ns(timestamp_ns: int) -> int:
    return int(timestamp_ns) // DAY_NS * DAY_NS


@dataclass
class _Ohlcv:
    open: float
    high: float
    low: float
    close: float
    volume: float
    quote_volume: float
    first_observation_ns: int
    quote_fallback_count: int = 0

    def update(
        self, *, high: float, low: float, close: float, volume: float,
        quote_volume: float, used_fallback: bool,
    ) -> None:
        self.high = max(self.high, high)
        self.low = min(self.low, low)
        self.close = close
        self.volume += volume
        self.quote_volume += quote_volume
        self.quote_fallback_count += int(used_fallback)


class CryptoUtcSessionFeature(_AbstractFeature):
    """One output from the versioned ``CRYPTO_UTC_SESSION_V1`` state."""

    def __init__(self, spec: FeatureSpec) -> None:
        super().__init__(spec)
        self._output = str(spec.params.get("output", "session_vwap"))
        self._bar_interval_ns = int(spec.params.get("bar_interval_ns", 60_000_000_000))
        self._opening_range_ns = int(spec.params.get("opening_range_minutes", 30)) * 60_000_000_000
        self._execution_lag_ns = int(spec.params.get("execution_lag_minutes", 0)) * 60_000_000_000
        self._execution_step_ns = int(spec.params.get("execution_step_ns", 60_000_000_000))
        if self._bar_interval_ns <= 0 or self._opening_range_ns <= 0 or self._execution_step_ns <= 0:
            raise ValueError("session intervals must be positive")
        if self._execution_lag_ns < 0:
            raise ValueError("execution lag cannot be negative")
        self._session_start: int | None = None
        self._current: _Ohlcv | None = None
        self._previous: _Ohlcv | None = None
        self._opening_high: float | None = None
        self._opening_low: float | None = None

    def warmup_required(self) -> WarmupRequirement:
        return WarmupRequirement(n_events=1, unit="bars", mandatory=False)

    @property
    def is_ready(self) -> bool:
        return self._value() is not None

    def reset(self) -> None:
        self._session_start = None
        self._current = None
        self._previous = None
        self._opening_high = None
        self._opening_low = None
        self._reset_base()

    def _roll(self, session_start: int) -> None:
        if self._session_start is not None:
            self._previous = (
                self._current if session_start - self._session_start == DAY_NS else None
            )
        self._session_start = session_start
        self._current = None
        self._opening_high = None
        self._opening_low = None

    def _value(self) -> float | int | bool | None:
        current, previous = self._current, self._previous
        if self._output == "session_open":
            return current.open if current else None
        if self._output == "session_start_ns":
            return self._session_start
        if self._output == "session_open_time_ns":
            return current.first_observation_ns if current else None
        if self._output == "session_high":
            return current.high if current else None
        if self._output == "session_low":
            return current.low if current else None
        if self._output == "session_vwap":
            return current.quote_volume / current.volume if current and current.volume > 0 else None
        if self._output == "session_vwap_quote_fallback_count":
            return current.quote_fallback_count if current else 0
        if self._output == "previous_open":
            return previous.open if previous else None
        if self._output == "previous_high":
            return previous.high if previous else None
        if self._output == "previous_low":
            return previous.low if previous else None
        if self._output == "previous_close":
            return previous.close if previous else None
        if self._output == "previous_volume":
            return previous.volume if previous else None
        if self._output == "previous_quote_volume":
            return previous.quote_volume if previous else None
        if self._output == "previous_range":
            return previous.high - previous.low if previous else None
        if self._output == "boundary_return":
            return (
                current.open / previous.close - 1.0
                if current and previous and previous.close != 0 else None
            )
        if self._output == "opening_range_high":
            return self._opening_high
        if self._output == "opening_range_low":
            return self._opening_low
        if self._output == "opening_range_ready":
            if self._session_start is None or self._current is None:
                return False
            return self._last_trigger_ts >= self._session_start + self._opening_range_ns
        if self._output == "session_entry_allowed":
            # The decision must still be executable strictly before the UTC
            # boundary.  Once the flatten decision is due, keep entries
            # disabled for the remainder of that session; otherwise a lagged
            # entry at 23:59 could fill at 00:00 after the flatten fill.
            within_day = self._last_trigger_ts % DAY_NS
            last_entry_decision = DAY_NS - self._execution_lag_ns - self._execution_step_ns
            return 0 < within_day < last_entry_decision
        raise ValueError(f"unknown crypto session output {self._output!r}")

    def update(self, event: Any) -> FeatureUpdate:
        self._event_count += 1
        decision_ts = _ts_ns(event, self._spec.trigger.time_semantics)
        # A completed bar is [decision_ts - interval, decision_ts).  Subtracting
        # one nanosecond gives the correct half-open UTC session at midnight.
        observation_ts = decision_ts - 1 if getattr(event, "event_type", "bar") == "bar" else decision_ts
        session_start = utc_session_start_ns(observation_ts)
        if session_start != self._session_start:
            self._roll(session_start)
        open_ = _field(event, "open")
        high = _field(event, "high")
        low = _field(event, "low")
        close = _field(event, "close")
        volume = _field(event, "volume")
        if None in (open_, high, low, close, volume):
            return self._missing_field("OHLCV")
        source_quote = _field(event, "quote_volume")
        used_fallback = source_quote is None
        quote_volume = close * volume if used_fallback else source_quote
        observation_start = decision_ts - self._bar_interval_ns
        if self._current is None:
            self._current = _Ohlcv(
                open=open_, high=high, low=low, close=close, volume=volume,
                quote_volume=quote_volume, first_observation_ns=observation_start,
                quote_fallback_count=int(used_fallback),
            )
        else:
            self._current.update(
                high=high, low=low, close=close, volume=volume,
                quote_volume=quote_volume, used_fallback=used_fallback,
            )
        if observation_start < session_start + self._opening_range_ns:
            self._opening_high = high if self._opening_high is None else max(self._opening_high, high)
            self._opening_low = low if self._opening_low is None else min(self._opening_low, low)
        self._last_trigger_ts = decision_ts
        value = self._value()
        return self._emit(value, value is not None, True, source_event_time_ns=decision_ts)

    def state_dict(self) -> dict:
        def encode(value: _Ohlcv | None) -> dict[str, object] | None:
            return None if value is None else dict(value.__dict__)
        return {
            **self._base_state(), "session_start": self._session_start,
            "current": encode(self._current), "previous": encode(self._previous),
            "opening_high": self._opening_high, "opening_low": self._opening_low,
        }

    def load_state_dict(self, state: dict) -> None:
        self._load_base(state)
        self._session_start = state.get("session_start")
        self._current = _Ohlcv(**state["current"]) if state.get("current") else None
        self._previous = _Ohlcv(**state["previous"]) if state.get("previous") else None
        self._opening_high = state.get("opening_high")
        self._opening_low = state.get("opening_low")


class SessionFlattenDueFeature(_AbstractFeature):
    """True at the decision time that fills on the last executable pre-boundary bar."""

    def __init__(self, spec: FeatureSpec) -> None:
        super().__init__(spec)
        self._lag_ns = int(spec.params.get("execution_lag_minutes", 0)) * 60_000_000_000
        self._step_ns = int(spec.params.get("execution_step_ns", 60_000_000_000))
        if self._lag_ns < 0 or self._step_ns <= 0:
            raise ValueError("invalid session flatten lag/step")

    def warmup_required(self) -> WarmupRequirement:
        return WarmupRequirement(1, mandatory=False)

    @property
    def is_ready(self) -> bool:
        return True

    def reset(self) -> None:
        self._reset_base()

    def update(self, event: Any) -> FeatureUpdate:
        self._event_count += 1
        ts_ns = _ts_ns(event, self._spec.trigger.time_semantics)
        due = (ts_ns + self._lag_ns + self._step_ns) % DAY_NS == 0
        return self._emit(due, True, True, source_event_time_ns=ts_ns)

    def state_dict(self) -> dict:
        return self._base_state()

    def load_state_dict(self, state: dict) -> None:
        self._load_base(state)


class CompletedTimeframeFeature(_AbstractFeature):
    """SMA or confirmed-fractal pulse from completed aligned child bars."""

    def __init__(self, spec: FeatureSpec) -> None:
        super().__init__(spec)
        self._output = str(spec.params["output"])
        self._frame_ns = int(spec.params["timeframe_minutes"]) * 60_000_000_000
        self._window = int(spec.params.get("window", spec.window or 1))
        if self._frame_ns <= 0 or self._window <= 0:
            raise ValueError("completed timeframe and window must be positive")
        self._bucket: int | None = None
        self._ohlc: list[float] | None = None
        self._closes: deque[float] = deque(maxlen=self._window)
        self._highs: deque[float] = deque(maxlen=5)
        self._lows: deque[float] = deque(maxlen=5)
        self._latest: float | None = None

    def warmup_required(self) -> WarmupRequirement:
        bars = self._window if self._output == "sma" else 5
        return WarmupRequirement(bars, unit="completed_timeframe_bars")

    @property
    def is_ready(self) -> bool:
        return self._latest is not None

    def reset(self) -> None:
        self._bucket = None
        self._ohlc = None
        self._closes.clear(); self._highs.clear(); self._lows.clear()
        self._latest = None
        self._reset_base()

    def _finish(self) -> None:
        if self._ohlc is None:
            return
        _open, high, low, close = self._ohlc
        if self._output == "sma":
            self._closes.append(close)
            self._latest = sum(self._closes) / self._window if len(self._closes) == self._window else None
        else:
            self._highs.append(high); self._lows.append(low)
            self._latest = 0.0
            if len(self._highs) == 5:
                if self._output == "upper_fractal_pulse":
                    self._latest = float(self._highs[2] > max(self._highs[0], self._highs[1], self._highs[3], self._highs[4]))
                elif self._output == "lower_fractal_pulse":
                    self._latest = float(self._lows[2] < min(self._lows[0], self._lows[1], self._lows[3], self._lows[4]))
                else:
                    raise ValueError(f"unknown completed timeframe output {self._output!r}")

    def update(self, event: Any) -> FeatureUpdate:
        self._event_count += 1
        ts_ns = _ts_ns(event, self._spec.trigger.time_semantics)
        observation_ts = ts_ns - 1
        bucket = observation_ts // self._frame_ns * self._frame_ns
        open_, high, low, close = (_field(event, name) for name in ("open", "high", "low", "close"))
        if None in (open_, high, low, close):
            return self._missing_field("OHLC")
        if self._bucket is not None and bucket != self._bucket:
            self._finish()
            self._ohlc = None
        self._bucket = bucket
        if self._ohlc is None:
            self._ohlc = [open_, high, low, close]
        else:
            self._ohlc[1] = max(self._ohlc[1], high)
            self._ohlc[2] = min(self._ohlc[2], low)
            self._ohlc[3] = close
        completed = ts_ns >= bucket + self._frame_ns
        if completed:
            self._finish()
            self._bucket = None
            self._ohlc = None
        elif self._output.endswith("pulse"):
            self._latest = 0.0 if len(self._highs) == 5 else None
        return self._emit(self._latest, self._latest is not None, True, source_event_time_ns=ts_ns)

    def state_dict(self) -> dict:
        return {
            **self._base_state(), "bucket": self._bucket, "ohlc": self._ohlc,
            "closes": list(self._closes), "highs": list(self._highs),
            "lows": list(self._lows), "latest": self._latest,
        }

    def load_state_dict(self, state: dict) -> None:
        self._load_base(state)
        self._bucket = state.get("bucket"); self._ohlc = state.get("ohlc")
        self._closes = deque(state.get("closes", []), maxlen=self._window)
        self._highs = deque(state.get("highs", []), maxlen=5)
        self._lows = deque(state.get("lows", []), maxlen=5)
        self._latest = state.get("latest")
