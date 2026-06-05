"""
Reusable incremental state containers.

Each container maintains running statistics and updates in O(1) amortized time.
No full-window scanning on push — running sums are kept in sync with element
additions and evictions. This is the property that keeps the hot path fast:
the cost of one update does not grow with window size.

State containers are internal building blocks for FeatureBase implementations.
Strategy code never touches them directly.

Containers
----------
RollingWindowState  — fixed-size ring buffer; running sum and optional sum-of-squares
TimeWindowState     — timestamp-keyed deque; evicts entries outside a time window
EWMAState           — exponentially weighted moving average; single float state
VWAPState           — cumulative price×volume / volume; rolling or unbounded
"""
from __future__ import annotations

import math
from collections import deque


class RollingWindowState:
    """Fixed-size ring buffer with O(1) running sum and optional sum-of-squares.

    When the window is full the oldest element is evicted before the new one
    is appended, keeping both ``_sum`` and ``_sum_sq`` in sync with one
    subtraction and one addition each — regardless of window size.

    Parameters
    ----------
    maxlen : int
        Maximum number of elements retained.
    track_squares : bool
        If True, maintain a running sum-of-squares, enabling O(1) variance/std.
        Costs one extra float multiplication per push.
    """

    def __init__(self, maxlen: int, *, track_squares: bool = False) -> None:
        self._maxlen = maxlen
        self._track_squares = track_squares
        self._buf: deque[float] = deque(maxlen=maxlen)
        self._sum: float = 0.0
        self._sum_sq: float = 0.0

    # ------------------------------------------------------------------
    # Write — O(1)
    # ------------------------------------------------------------------

    def push(self, value: float) -> None:
        """Append value; evict oldest when full."""
        if len(self._buf) == self._maxlen:
            old = self._buf[0]
            self._sum -= old
            if self._track_squares:
                self._sum_sq -= old * old
        self._buf.append(value)
        self._sum += value
        if self._track_squares:
            self._sum_sq += value * value

    # ------------------------------------------------------------------
    # Read — O(1) aggregates
    # ------------------------------------------------------------------

    @property
    def count(self) -> int:
        return len(self._buf)

    @property
    def is_full(self) -> bool:
        return len(self._buf) == self._maxlen

    @property
    def sum(self) -> float:
        return self._sum

    @property
    def mean(self) -> float | None:
        n = len(self._buf)
        return self._sum / n if n else None

    @property
    def variance(self) -> float | None:
        """Bessel-corrected (sample) variance. Requires track_squares=True."""
        n = len(self._buf)
        if n < 2 or not self._track_squares:
            return None
        # Population variance from running sums, then correct to sample variance
        mean = self._sum / n
        pop_var = self._sum_sq / n - mean * mean
        return max(0.0, pop_var) * n / (n - 1)

    @property
    def std(self) -> float | None:
        """Sample standard deviation. Requires track_squares=True."""
        v = self.variance
        return math.sqrt(v) if v is not None else None

    @property
    def min(self) -> float | None:
        """O(window) scan — acceptable for windows < a few thousand bars."""
        return min(self._buf) if self._buf else None

    @property
    def max(self) -> float | None:
        """O(window) scan — acceptable for windows < a few thousand bars."""
        return max(self._buf) if self._buf else None

    @property
    def values(self) -> list[float]:
        return list(self._buf)

    # ------------------------------------------------------------------
    # Management
    # ------------------------------------------------------------------

    def reset(self) -> None:
        self._buf.clear()
        self._sum = 0.0
        self._sum_sq = 0.0

    def state_dict(self) -> dict:
        return {
            "maxlen": self._maxlen,
            "track_squares": self._track_squares,
            "values": list(self._buf),
            "sum": self._sum,
            "sum_sq": self._sum_sq,
        }

    def load_state_dict(self, state: dict) -> None:
        self._maxlen = state["maxlen"]
        self._track_squares = state["track_squares"]
        self._buf = deque(state["values"], maxlen=self._maxlen)
        self._sum = state["sum"]
        self._sum_sq = state["sum_sq"]


class TimeWindowState:
    """Timestamped deque with time-based eviction and O(1) running sum.

    On each push, entries older than (ts_ms - window_ms) are popped from
    the front before appending the new entry. The running sum is updated
    with a subtraction per eviction and one addition per push.

    Parameters
    ----------
    window_ms : int
        Sliding window length in milliseconds.
    """

    def __init__(self, window_ms: int) -> None:
        self._window_ms = window_ms
        self._entries: deque[tuple[int, float]] = deque()
        self._sum: float = 0.0

    # ------------------------------------------------------------------
    # Write — O(amortized 1)
    # ------------------------------------------------------------------

    def push(self, ts_ms: int, value: float) -> None:
        """Append (ts_ms, value); evict all entries outside the window."""
        cutoff = ts_ms - self._window_ms
        while self._entries and self._entries[0][0] <= cutoff:
            self._sum -= self._entries.popleft()[1]
        self._entries.append((ts_ms, value))
        self._sum += value

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    @property
    def count(self) -> int:
        return len(self._entries)

    @property
    def sum(self) -> float:
        return self._sum

    @property
    def mean(self) -> float | None:
        n = len(self._entries)
        return self._sum / n if n else None

    @property
    def values(self) -> list[float]:
        return [v for _, v in self._entries]

    @property
    def timestamps(self) -> list[int]:
        return [ts for ts, _ in self._entries]

    # ------------------------------------------------------------------
    # Management
    # ------------------------------------------------------------------

    def reset(self) -> None:
        self._entries.clear()
        self._sum = 0.0

    def state_dict(self) -> dict:
        return {
            "window_ms": self._window_ms,
            "entries": list(self._entries),
            "sum": self._sum,
        }

    def load_state_dict(self, state: dict) -> None:
        self._window_ms = state["window_ms"]
        self._entries = deque(tuple(e) for e in state["entries"])
        self._sum = state["sum"]


class EWMAState:
    """Exponentially weighted moving average — O(1) per update.

    Formula: ema_t = alpha * x_t + (1 - alpha) * ema_{t-1}
    Initial value is set to x_0 (no half-period bias at startup).

    Parameters
    ----------
    span : int | None
        Span n → alpha = 2 / (n + 1). Mutually exclusive with alpha.
    alpha : float | None
        Explicit smoothing factor in (0, 1]. Mutually exclusive with span.
    """

    def __init__(self, span: int | None = None, alpha: float | None = None) -> None:
        if alpha is not None:
            self._alpha = float(alpha)
        elif span is not None:
            self._alpha = 2.0 / (span + 1)
        else:
            raise ValueError("Either span or alpha must be specified")
        self._value: float | None = None
        self._count: int = 0

    def push(self, value: float) -> None:
        self._count += 1
        if self._value is None:
            self._value = value
        else:
            self._value = self._alpha * value + (1.0 - self._alpha) * self._value

    @property
    def value(self) -> float | None:
        return self._value

    @property
    def alpha(self) -> float:
        return self._alpha

    @property
    def count(self) -> int:
        return self._count

    def reset(self) -> None:
        self._value = None
        self._count = 0

    def state_dict(self) -> dict:
        return {"alpha": self._alpha, "value": self._value, "count": self._count}

    def load_state_dict(self, state: dict) -> None:
        self._alpha = state["alpha"]
        self._value = state["value"]
        self._count = state["count"]


class VWAPState:
    """VWAP state: cumulative price×volume and volume, with optional eviction.

    Modes:
    - Unbounded (session VWAP): window=None, window_ms=None.
    - Count-based rolling: window=N, uses deque(maxlen=N).
    - Time-based rolling: window_ms=M, evicts entries older than M ms.

    vwap = Σ(price × volume) / Σ(volume)

    Parameters
    ----------
    window : int | None
        Count-based rolling window. None = unbounded or time-based.
    window_ms : int | None
        Time-based rolling window in milliseconds. None = unbounded or count-based.
    """

    def __init__(
        self,
        window: int | None = None,
        window_ms: int | None = None,
    ) -> None:
        self._window = window
        self._window_ms = window_ms
        maxlen = window  # None → unbounded deque
        self._pv_buf: deque[float] = deque(maxlen=maxlen)
        self._v_buf: deque[float] = deque(maxlen=maxlen)
        self._ts_buf: deque[int] = deque()
        self._pv_sum: float = 0.0
        self._v_sum: float = 0.0

    def push(self, price: float, volume: float, ts_ms: int = 0) -> None:
        """Update VWAP state. O(1) amortized."""
        pv = price * volume

        # Count-based: evict before append (deque.maxlen handles the cap, but
        # we must subtract the outgoing element from running totals first).
        if self._window is not None and len(self._pv_buf) == self._window:
            self._pv_sum -= self._pv_buf[0]
            self._v_sum -= self._v_buf[0]

        # Time-based: evict entries older than the window.
        if self._window_ms is not None:
            cutoff = ts_ms - self._window_ms
            while self._ts_buf and self._ts_buf[0] <= cutoff:
                self._pv_sum -= self._pv_buf.popleft()
                self._v_sum -= self._v_buf.popleft()
                self._ts_buf.popleft()

        self._pv_buf.append(pv)
        self._v_buf.append(volume)
        if self._window_ms is not None:
            self._ts_buf.append(ts_ms)
        self._pv_sum += pv
        self._v_sum += volume

    @property
    def vwap(self) -> float | None:
        return self._pv_sum / self._v_sum if self._v_sum != 0.0 else None

    @property
    def count(self) -> int:
        return len(self._pv_buf)

    def reset(self) -> None:
        self._pv_buf.clear()
        self._v_buf.clear()
        self._ts_buf.clear()
        self._pv_sum = 0.0
        self._v_sum = 0.0

    def state_dict(self) -> dict:
        return {
            "window": self._window,
            "window_ms": self._window_ms,
            "pv_buf": list(self._pv_buf),
            "v_buf": list(self._v_buf),
            "ts_buf": list(self._ts_buf),
            "pv_sum": self._pv_sum,
            "v_sum": self._v_sum,
        }

    def load_state_dict(self, state: dict) -> None:
        self._window = state["window"]
        self._window_ms = state["window_ms"]
        maxlen = self._window
        self._pv_buf = deque(state["pv_buf"], maxlen=maxlen)
        self._v_buf = deque(state["v_buf"], maxlen=maxlen)
        self._ts_buf = deque(state["ts_buf"])
        self._pv_sum = state["pv_sum"]
        self._v_sum = state["v_sum"]
