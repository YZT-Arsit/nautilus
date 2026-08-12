from __future__ import annotations

from feature_engine.api import FeatureSnapshot

from strategies.workbook_parametric.config import WorkbookParametricConfig

BUY, SELL, HOLD = "BUY", "SELL", "HOLD"


class WorkbookParametricStrategy:
    """Reviewed workbook rules; ``decision_position`` is target state, never a fill."""

    def __init__(self, config: WorkbookParametricConfig) -> None:
        self.config = config
        self.decision_position = 0
        self._previous: dict[str, float] = {}
        self._extreme_count = 0
        self._holding_bars = 0

    def _value(self, snapshot: FeatureSnapshot, name: str) -> float | None:
        value = snapshot.value(name)
        return None if value is None else float(value)

    def _set(self, target: int) -> str:
        changed = target != self.decision_position
        self.decision_position = target
        self._holding_bars = 0 if changed else self._holding_bars
        return BUY if target > 0 else SELL if target < 0 else HOLD

    def on_snapshot(self, snapshot: FeatureSnapshot) -> str:
        family = self.config.family
        close = self._value(snapshot, "workbook_close")
        if close is None:
            return HOLD
        if self.decision_position:
            self._holding_bars += 1
        if family == "sma_crossover":
            fast, slow = self._value(snapshot, "workbook_fast"), self._value(snapshot, "workbook_slow")
            previous_fast, previous_slow = self._previous.get("fast"), self._previous.get("slow")
            self._previous.update(fast=fast, slow=slow)
            if None in (fast, slow, previous_fast, previous_slow):
                return HOLD
            if previous_fast <= previous_slow and fast > slow:
                return self._set(1)
            if previous_fast >= previous_slow and fast < slow:
                return self._set(-1)
            if self.config.maximum_holding_bars and self._holding_bars > self.config.maximum_holding_bars:
                return self._set(0)
            return HOLD
        if family == "ma_envelope":
            middle = self._value(snapshot, "workbook_middle")
            if middle is None:
                return HOLD
            previous_close, previous_middle = self._previous.get("close"), self._previous.get("middle")
            self._previous.update(close=close, middle=middle)
            if self.decision_position > 0 and close <= middle:
                return self._set(0)
            if self.decision_position < 0 and close >= middle:
                return self._set(0)
            if previous_close is not None and previous_middle is not None:
                fraction = self.config.envelope_fraction
                if previous_close <= previous_middle * (1 + fraction) and close > middle * (1 + fraction):
                    return self._set(1)
                if previous_close >= previous_middle * (1 - fraction) and close < middle * (1 - fraction):
                    return self._set(-1)
            return HOLD
        if family == "bollinger":
            middle, percent_b = self._value(snapshot, "workbook_middle"), self._value(snapshot, "workbook_percent_b")
            if middle is None or percent_b is None:
                return HOLD
            if self.decision_position > 0 and close <= middle:
                return self._set(0)
            if self.decision_position < 0 and close >= middle:
                return self._set(0)
            direction = 1 if percent_b > 1 else -1 if percent_b < 0 else 0
            previous_direction = int(self._previous.get("extreme_direction", 0))
            self._extreme_count = self._extreme_count + 1 if direction and direction == previous_direction else (1 if direction else 0)
            self._previous["extreme_direction"] = float(direction)
            if self._extreme_count >= self.config.consecutive_bars:
                return self._set(direction)
            return HOLD
        if family == "atr_channel":
            middle, atr = self._value(snapshot, "workbook_middle"), self._value(snapshot, "workbook_atr")
            if middle is None or atr is None:
                return HOLD
            if self.decision_position > 0 and close <= middle:
                return self._set(0)
            if self.decision_position < 0 and close >= middle:
                return self._set(0)
            upper, lower = middle + self.config.multiplier * atr, middle - self.config.multiplier * atr
            previous_close = self._previous.get("close")
            previous_upper, previous_lower = self._previous.get("upper"), self._previous.get("lower")
            self._previous.update(close=close, upper=upper, lower=lower)
            if previous_close is not None and previous_upper is not None and previous_close <= previous_upper and close > upper:
                return self._set(1)
            if previous_close is not None and previous_lower is not None and previous_close >= previous_lower and close < lower:
                return self._set(-1)
            return HOLD
        raise ValueError(f"unsupported exact workbook family: {family}")

    def on_warmup_snapshot(self, snapshot: FeatureSnapshot) -> None:
        # Seed decision inputs across the warmup/live boundary, but never change
        # the target position or assume that an order filled.
        close = self._value(snapshot, "workbook_close")
        if close is not None:
            self._previous["close"] = close
        if self.config.family == "sma_crossover":
            fast = self._value(snapshot, "workbook_fast")
            slow = self._value(snapshot, "workbook_slow")
            if fast is not None and slow is not None:
                self._previous.update(fast=fast, slow=slow)
        elif self.config.family == "ma_envelope":
            middle = self._value(snapshot, "workbook_middle")
            if middle is not None:
                self._previous["middle"] = middle
        elif self.config.family == "atr_channel":
            middle = self._value(snapshot, "workbook_middle")
            atr = self._value(snapshot, "workbook_atr")
            if close is not None and middle is not None and atr is not None:
                self._previous.update(
                    upper=middle + self.config.multiplier * atr,
                    lower=middle - self.config.multiplier * atr,
                )
        elif self.config.family == "bollinger":
            percent_b = self._value(snapshot, "workbook_percent_b")
            if percent_b is not None:
                direction = 1 if percent_b > 1 else -1 if percent_b < 0 else 0
                previous_direction = int(self._previous.get("extreme_direction", 0))
                self._extreme_count = self._extreme_count + 1 if direction and direction == previous_direction else (1 if direction else 0)
                self._previous["extreme_direction"] = float(direction)
