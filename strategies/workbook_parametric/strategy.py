from __future__ import annotations

from feature_engine.api import FeatureSnapshot
from strategy_framework.execution.intents import PlannedSignal, TradeAction
from strategy_framework.conditions import cross_above, cross_below
from strategy_framework.modules import GridPyramidState, PyramidDirection
from strategy_framework.semantic_contracts import turn_down, turn_up

from strategies.workbook_parametric.config import WorkbookParametricConfig

BUY, SELL, HOLD, EXIT = "BUY", "SELL", "HOLD", "EXIT"


class WorkbookParametricStrategy:
    """Reviewed workbook rules; ``decision_position`` is target state, never a fill."""

    def __init__(self, config: WorkbookParametricConfig) -> None:
        self.config = config
        self.decision_position = 0.0
        self._previous: dict[str, float] = {}
        self._extreme_count = 0
        self._opposite_extreme_count = 0
        self._holding_bars = 0
        self.execution_entry_price: float | None = None
        self._grid = GridPyramidState(
            layers=config.grid_layers,
            step_atr=config.entry_distance_multiple,
            layer_fraction=config.layer_fraction,
        ) if config.family == "donchian_pyramid" else None

    def synchronize_execution(self, *, position: float, fill_price: float) -> None:
        if self._grid is not None:
            self._grid.synchronize_fill(position=position, fill_price=fill_price)

    def _value(self, snapshot: FeatureSnapshot, name: str) -> float | None:
        value = snapshot.value(name)
        return None if value is None else float(value)

    def _set(self, target: float) -> PlannedSignal:
        target = float(target)
        if abs(target) > 1.0 + 1e-12:
            raise ValueError("workbook target exposure cannot exceed 1x")
        previous = float(self.decision_position)
        if target == 0 or previous * target <= 0 or abs(target) > abs(previous):
            self._previous.pop("session_reduced", None)
        changed = target != previous
        self.decision_position = target
        self._holding_bars = 0 if changed else self._holding_bars
        if not changed:
            return PlannedSignal(HOLD, ())
        actions: list[TradeAction] = []
        if previous != 0 and (target == 0 or previous * target < 0):
            actions.append(
                TradeAction(
                    side=SELL if previous > 0 else BUY,
                    close_all=True,
                    reason="workbook_target_flatten",
                )
            )
        if target != 0 and (previous == 0 or previous * target < 0):
            actions.append(
                TradeAction(
                    side=BUY if target > 0 else SELL,
                    quantity=abs(target),
                    reason=f"workbook_target_{'long' if target > 0 else 'short'}",
                )
            )
        elif target != 0 and previous * target > 0:
            delta = target - previous
            if abs(delta) > 1e-12:
                actions.append(
                    TradeAction(
                        side=BUY if delta > 0 else SELL,
                        quantity=abs(delta),
                        reason="workbook_target_exposure_adjustment",
                    )
                )
        label = actions[-1].side if actions and not actions[-1].close_all else EXIT
        return PlannedSignal(label, actions)

    def _on_session_snapshot(self, snapshot: FeatureSnapshot, close: float) -> PlannedSignal | str:
        vwap = self._value(snapshot, "workbook_session_vwap")
        session_start = self._value(snapshot, "workbook_session_start")
        flatten = bool(self._value(snapshot, "workbook_session_flatten") or False)
        entry_allowed = bool(self._value(snapshot, "workbook_session_entry_allowed") or False)
        if session_start is not None and session_start != self._previous.get("session_start"):
            self._previous.update(
                session_start=session_start, session_above_count=0.0,
                session_below_count=0.0, session_reduced=0.0,
            )
        if flatten:
            return self._set(0) if self.decision_position else HOLD
        if vwap is None:
            return HOLD
        above = int(self._previous.get("session_above_count", 0))
        below = int(self._previous.get("session_below_count", 0))
        above = above + 1 if close > vwap else 0
        below = below + 1 if close < vwap else 0
        self._previous["session_above_count"] = float(above)
        self._previous["session_below_count"] = float(below)

        if self.config.family == "session_vwap_roc_turn":
            roc = self._value(snapshot, "workbook_roc")
            previous = self._previous.get("session_roc")
            self._previous["session_roc"] = roc if roc is not None else 0.0
            if roc is None or previous is None:
                return HOLD
            if self.decision_position > 0 and close < vwap:
                return self._set(0)
            if self.decision_position < 0 and close > vwap:
                return self._set(0)
            if entry_allowed and close > vwap and previous <= 0 < roc:
                return self._set(1)
            if entry_allowed and close < vwap and previous >= 0 > roc:
                return self._set(-1)
            return HOLD

        if self.config.family == "session_vwap_ma_trend":
            moving_average = self._value(snapshot, "workbook_completed_ma")
            atr = self._value(snapshot, "workbook_atr")
            if moving_average is None or atr is None:
                return HOLD
            if self.decision_position > 0 and close < vwap:
                return self._set(0)
            if self.decision_position < 0 and close > vwap:
                return self._set(0)
            reduced = bool(self._previous.get("session_reduced", 0.0))
            if self.decision_position and not reduced and abs(close - vwap) > self.config.multiplier * atr:
                self._previous["session_reduced"] = 1.0
                return self._set(self.decision_position * (1.0 - self.config.reduction_fraction))
            if entry_allowed and self.decision_position == 0 and above >= self.config.consecutive_bars and close > moving_average:
                return self._set(1)
            if entry_allowed and self.decision_position == 0 and below >= self.config.consecutive_bars and close < moving_average:
                return self._set(-1)
            return HOLD

        if self.config.family == "session_vwap_volume_mean":
            volume = self._value(snapshot, "workbook_volume")
            volume_mean = self._value(snapshot, "workbook_volume_mean")
            previous_volume = self._previous.get("session_volume")
            previous_mean = self._previous.get("session_volume_mean")
            if volume is not None:
                self._previous["session_volume"] = volume
            if volume_mean is not None:
                self._previous["session_volume_mean"] = volume_mean
            if None in (volume, volume_mean, previous_volume, previous_mean):
                return HOLD
            reduced = bool(self._previous.get("session_reduced", 0.0))
            if self.decision_position and volume < volume_mean and not reduced:
                self._previous["session_reduced"] = 1.0
                return self._set(self.decision_position * (1.0 - self.config.reduction_fraction))
            if entry_allowed and self.decision_position == 0 and above >= self.config.consecutive_bars and previous_volume <= previous_mean < volume:
                return self._set(1)
            if entry_allowed and self.decision_position == 0 and below >= self.config.consecutive_bars and volume < volume_mean:
                return self._set(-1)
            return HOLD

        if self.config.family in {"session_vwap_fractal", "session_vwap_mtf_fractal"}:
            frames = (15,) if self.config.family == "session_vwap_fractal" else (5, 15, 30)
            upper = [self._value(snapshot, f"workbook_upper_fractal_{frame}m") for frame in frames]
            lower = [self._value(snapshot, f"workbook_lower_fractal_{frame}m") for frame in frames]
            upper_trigger = all(value is not None and value > 0 for value in upper)
            lower_trigger = all(value is not None and value > 0 for value in lower)
            if self.decision_position > 0 and upper_trigger:
                return self._set(0)
            if self.decision_position < 0 and lower_trigger:
                return self._set(0)
            if self.config.family == "session_vwap_fractal":
                atr = self._value(snapshot, "workbook_atr")
                reduced = bool(self._previous.get("session_reduced", 0.0))
                if (
                    self.decision_position and atr is not None and not reduced
                    and abs(close - vwap) > self.config.multiplier * atr
                ):
                    self._previous["session_reduced"] = 1.0
                    return self._set(self.decision_position * (1.0 - self.config.reduction_fraction))
            if entry_allowed and close > vwap and lower_trigger:
                return self._set(1)
            if entry_allowed and close < vwap and upper_trigger:
                return self._set(-1)
            return HOLD
        raise ValueError(f"unsupported session family: {self.config.family}")

    def on_snapshot(self, snapshot: FeatureSnapshot) -> str:
        family = self.config.family
        close = self._value(snapshot, "workbook_close")
        if close is None:
            return HOLD
        if family.startswith("session_"):
            return self._on_session_snapshot(snapshot, close)
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
        if family == "bollinger_width_cross":
            fast = self._value(snapshot, "workbook_bbw_fast")
            slow = self._value(snapshot, "workbook_bbw_slow")
            previous_fast = self._previous.get("bbw_fast")
            previous_slow = self._previous.get("bbw_slow")
            self._previous.update(bbw_fast=fast, bbw_slow=slow)
            if None in (fast, slow, previous_fast, previous_slow):
                return HOLD
            if cross_above(previous_fast, previous_slow, fast, slow):
                return self._set(1)
            if cross_below(previous_fast, previous_slow, fast, slow):
                return self._set(-1)
            return HOLD
        if family == "sma_price_cross":
            middle = self._value(snapshot, "workbook_middle")
            previous_close, previous_middle = self._previous.get("close"), self._previous.get("middle")
            self._previous.update(close=close, middle=middle)
            if None in (middle, previous_close, previous_middle):
                return HOLD
            if cross_above(previous_close, previous_middle, close, middle):
                return self._set(1)
            if cross_below(previous_close, previous_middle, close, middle):
                return self._set(-1)
            return HOLD
        if family == "ema_crossover":
            fast = self._value(snapshot, "workbook_fast")
            slow = self._value(snapshot, "workbook_slow")
            previous_fast, previous_slow = self._previous.get("fast"), self._previous.get("slow")
            self._previous.update(fast=fast, slow=slow)
            if None in (fast, slow, previous_fast, previous_slow):
                return HOLD
            if cross_above(previous_fast, previous_slow, fast, slow):
                return self._set(1)
            if cross_below(previous_fast, previous_slow, fast, slow):
                return self._set(-1)
            return HOLD
        if family == "ma_cross_slope_atr_exit":
            fast = self._value(snapshot, "workbook_fast")
            slow = self._value(snapshot, "workbook_slow")
            atr = self._value(snapshot, "workbook_atr")
            previous_fast, previous_slow = self._previous.get("fast"), self._previous.get("slow")
            older_fast, older_slow = self._previous.get("fast_older"), self._previous.get("slow_older")
            self._previous.update(
                fast_older=previous_fast, slow_older=previous_slow, fast=fast, slow=slow,
            )
            if None in (fast, slow, atr, previous_fast, previous_slow):
                return HOLD
            stop_hit = profit_hit = False
            if self.execution_entry_price is not None:
                stop_hit = self.config.stop_multiple > 0 and (
                    (
                        self.decision_position > 0
                        and close <= self.execution_entry_price - self.config.stop_multiple * atr
                    ) or (
                        self.decision_position < 0
                        and close >= self.execution_entry_price + self.config.stop_multiple * atr
                    )
                )
                profit_hit = self.config.take_profit_multiple > 0 and (
                    (self.decision_position > 0
                     and close >= self.execution_entry_price + self.config.take_profit_multiple * atr)
                    or (self.decision_position < 0
                        and close <= self.execution_entry_price - self.config.take_profit_multiple * atr)
                )
            if self.decision_position and (stop_hit or profit_hit):
                return self._set(0)
            crossed_up = cross_above(previous_fast, previous_slow, fast, slow)
            crossed_down = cross_below(previous_fast, previous_slow, fast, slow)
            slopes_up = fast > previous_fast and slow > previous_slow
            slopes_down = fast < previous_fast and slow < previous_slow
            if crossed_up and slopes_up:
                return self._set(1)
            if crossed_down and slopes_down:
                return self._set(-1)
            if None not in (older_fast, older_slow) and self.decision_position:
                fast_prev_delta = previous_fast - older_fast
                slow_prev_delta = previous_slow - older_slow
                fast_now_delta = fast - previous_fast
                slow_now_delta = slow - previous_slow
                against_long = self.decision_position > 0 and (
                    (fast_prev_delta >= 0 > fast_now_delta)
                    or (slow_prev_delta >= 0 > slow_now_delta)
                )
                against_short = self.decision_position < 0 and (
                    (fast_prev_delta <= 0 < fast_now_delta)
                    or (slow_prev_delta <= 0 < slow_now_delta)
                )
                if against_long or against_short:
                    return self._set(
                        self.decision_position * (1.0 - self.config.reduction_fraction)
                    )
            return HOLD
        if family == "rsi_turn_candle":
            open_ = self._value(snapshot, "workbook_open")
            rsi = self._value(snapshot, "workbook_rsi")
            previous = self._previous.get("rsi")
            older = self._previous.get("rsi_older")
            self._previous.update(rsi_older=previous, rsi=rsi)
            if None in (open_, rsi, previous, older):
                return HOLD
            if self.decision_position > 0 and rsi >= self.config.upper_threshold:
                return self._set(0)
            if self.decision_position < 0 and rsi <= self.config.lower_threshold:
                return self._set(0)
            if self.decision_position > 0 and cross_above(
                previous, self.config.neutral_threshold, rsi, self.config.neutral_threshold,
            ):
                return self._set(self.decision_position * 0.5)
            if self.decision_position < 0 and cross_below(
                previous, self.config.neutral_threshold, rsi, self.config.neutral_threshold,
            ):
                return self._set(self.decision_position * 0.5)
            if previous < self.config.lower_threshold and turn_up(older, previous, rsi) and close > open_:
                return self._set(1)
            if previous > self.config.upper_threshold and turn_down(older, previous, rsi) and close < open_:
                return self._set(-1)
            return HOLD
        if family == "adx_ma_di_confluence":
            middle = self._value(snapshot, "workbook_middle")
            adx = self._value(snapshot, "workbook_adx")
            plus_di = self._value(snapshot, "workbook_plus_di")
            minus_di = self._value(snapshot, "workbook_minus_di")
            if None in (middle, adx, plus_di, minus_di):
                return HOLD
            if self.decision_position > 0 and (adx < self.config.adx_exit_threshold or close < middle):
                return self._set(0)
            if self.decision_position < 0 and (adx < self.config.adx_exit_threshold or close > middle):
                return self._set(0)
            if adx > self.config.adx_entry_threshold and close > middle and plus_di > minus_di:
                return self._set(1)
            if adx > self.config.adx_entry_threshold and close < middle and minus_di > plus_di:
                return self._set(-1)
            return HOLD
        if family == "macd_zero_trend":
            dif = self._value(snapshot, "workbook_macd_dif")
            signal_line = self._value(snapshot, "workbook_macd_signal")
            histogram = self._value(snapshot, "workbook_macd_histogram")
            previous_dif = self._previous.get("macd_dif")
            previous_signal = self._previous.get("macd_signal")
            previous_hist = self._previous.get("macd_hist")
            older_hist = self._previous.get("macd_hist_older")
            self._previous.update(
                macd_dif=dif, macd_signal=signal_line,
                macd_hist_older=previous_hist, macd_hist=histogram,
            )
            if None in (dif, signal_line, histogram, previous_dif, previous_signal):
                return HOLD
            if self.decision_position > 0 and cross_below(previous_dif, 0.0, dif, 0.0):
                return self._set(0)
            if self.decision_position < 0 and cross_above(previous_dif, 0.0, dif, 0.0):
                return self._set(0)
            if previous_dif > 0 and dif > 0 and cross_above(
                previous_dif, previous_signal, dif, signal_line,
            ):
                return self._set(1)
            if previous_dif < 0 and dif < 0 and cross_below(
                previous_dif, previous_signal, dif, signal_line,
            ):
                return self._set(-1)
            shrinking = (
                older_hist is not None and previous_hist is not None
                and abs(histogram) < abs(previous_hist) < abs(older_hist)
            )
            was_shrinking = bool(self._previous.get("macd_shrink_active", 0.0))
            self._previous["macd_shrink_active"] = float(shrinking)
            if self.decision_position and shrinking and not was_shrinking:
                return self._set(self.decision_position * 0.5)
            return HOLD
        if family == "macd_zero_persistent":
            dif = self._value(snapshot, "workbook_macd_dif")
            signal_line = self._value(snapshot, "workbook_macd_signal")
            histogram = self._value(snapshot, "workbook_macd_histogram")
            previous_dif = self._previous.get("macd_dif")
            previous_signal = self._previous.get("macd_signal")
            previous_hist = self._previous.get("macd_hist")
            older_hist = self._previous.get("macd_hist_older")
            self._previous.update(
                macd_dif=dif, macd_signal=signal_line,
                macd_hist_older=previous_hist, macd_hist=histogram,
            )
            if None in (dif, signal_line, histogram, previous_dif, previous_signal):
                return HOLD
            positive_count = int(self._previous.get("macd_positive_count", 0))
            negative_count = int(self._previous.get("macd_negative_count", 0))
            self._previous["macd_positive_count"] = float(positive_count + 1 if dif > 0 else 0)
            self._previous["macd_negative_count"] = float(negative_count + 1 if dif < 0 else 0)
            if self.decision_position > 0 and cross_below(previous_dif, 0, dif, 0):
                return self._set(0)
            if self.decision_position < 0 and cross_above(previous_dif, 0, dif, 0):
                return self._set(0)
            shrinking = (
                older_hist is not None and previous_hist is not None
                and abs(histogram) < abs(previous_hist) < abs(older_hist)
            )
            shrink_active = bool(self._previous.get("macd_shrink_active", 0))
            self._previous["macd_shrink_active"] = float(shrinking)
            if self.decision_position and shrinking and not shrink_active:
                return self._set(self.decision_position * 0.5)
            if (
                self._previous["macd_positive_count"] >= self.config.consecutive_bars
                and cross_above(previous_dif, previous_signal, dif, signal_line)
            ):
                return self._set(1)
            if (
                self._previous["macd_negative_count"] >= self.config.consecutive_bars
                and cross_below(previous_dif, previous_signal, dif, signal_line)
            ):
                return self._set(-1)
            return HOLD
        if family in {"ao_zero_persistent", "ema_ao_persistent"}:
            ao = self._value(snapshot, "workbook_ao")
            middle = self._value(snapshot, "workbook_middle") if family == "ema_ao_persistent" else None
            previous = self._previous.get("ao")
            older = self._previous.get("ao_older")
            previous_middle = self._previous.get("middle") if family == "ema_ao_persistent" else None
            self._previous.update(ao_older=previous, ao=ao)
            if middle is not None:
                self._previous["middle"] = middle
            if ao is None or previous is None or older is None or (
                family == "ema_ao_persistent" and previous_middle is None
            ):
                return HOLD
            if self.decision_position > 0 and (
                cross_below(previous, 0, ao, 0)
                or (family == "ema_ao_persistent" and middle < previous_middle)
            ):
                return self._set(0)
            if self.decision_position < 0 and (
                cross_above(previous, 0, ao, 0)
                or (family == "ema_ao_persistent" and middle > previous_middle)
            ):
                return self._set(0)
            shrinking = (
                self.decision_position > 0 and older < previous and ao < previous
            ) or (
                self.decision_position < 0 and older > previous and ao > previous
            )
            shrink_active = bool(self._previous.get("ao_shrink_active", 0))
            self._previous["ao_shrink_active"] = float(shrinking)
            if self.decision_position and shrinking and not shrink_active:
                return self._set(self.decision_position * 0.5)
            crossed_up = older <= 0 < previous
            crossed_down = older >= 0 > previous
            two_green = older < previous < ao
            two_red = older > previous > ao
            trend_up = family == "ao_zero_persistent" or middle > previous_middle
            trend_down = family == "ao_zero_persistent" or middle < previous_middle
            if crossed_up and two_green and trend_up:
                return self._set(1)
            if crossed_down and two_red and trend_down:
                return self._set(-1)
            return HOLD
        if family == "four_ma_stable_layered":
            fast = self._value(snapshot, "workbook_fast")
            middle = self._value(snapshot, "workbook_middle_ma")
            slow = self._value(snapshot, "workbook_slow")
            filter_ma = self._value(snapshot, "workbook_filter")
            previous_close = self._previous.get("close")
            previous_filter = self._previous.get("filter")
            older_filter = self._previous.get("filter_older")
            self._previous.update(
                close=close, filter_older=previous_filter, filter=filter_ma,
            )
            if None in (fast, middle, slow, filter_ma, previous_close, previous_filter):
                return HOLD
            long_order = fast > middle > slow > filter_ma
            short_order = fast < middle < slow < filter_ma
            above_all = close > max(fast, middle, slow, filter_ma)
            below_all = close < min(fast, middle, slow, filter_ma)
            if older_filter is not None and self.decision_position > 0 and turn_down(
                older_filter, previous_filter, filter_ma,
            ):
                return self._set(0)
            if older_filter is not None and self.decision_position < 0 and turn_up(
                older_filter, previous_filter, filter_ma,
            ):
                return self._set(0)
            order_valid = long_order if self.decision_position > 0 else short_order
            if self.decision_position and not order_valid:
                break_count = int(self._previous.get("ordering_break_count", 0.0)) + 1
                self._previous["ordering_break_count"] = float(break_count)
                return self._set(self.decision_position * 0.5 if break_count == 1 else 0)
            self._previous["ordering_break_count"] = 0.0
            stable_count = int(self._previous.get("stable_above_count", 0.0))
            stable_count = stable_count + 1 if long_order and above_all else 0
            self._previous["stable_above_count"] = float(stable_count)
            if stable_count >= self.config.consecutive_bars:
                return self._set(1)
            if short_order and below_all:
                return self._set(-1)
            return HOLD
        if family == "triple_sma_ordered":
            fast = self._value(snapshot, "workbook_fast")
            middle = self._value(snapshot, "workbook_middle_ma")
            slow = self._value(snapshot, "workbook_slow")
            previous_slow = self._previous.get("slow")
            older_slow = self._previous.get("slow_older")
            self._previous.update(slow_older=previous_slow, slow=slow)
            if None in (fast, middle, slow):
                return HOLD
            long_order = close > fast > middle > slow
            short_order = close < fast < middle < slow
            if self.decision_position > 0 and older_slow is not None and previous_slow is not None and turn_down(older_slow, previous_slow, slow):
                return self._set(0)
            if self.decision_position < 0 and older_slow is not None and previous_slow is not None and turn_up(older_slow, previous_slow, slow):
                return self._set(0)
            if self.decision_position > 0 and not fast > middle > slow:
                return self._set(self.decision_position * 0.5)
            if self.decision_position < 0 and not fast < middle < slow:
                return self._set(self.decision_position * 0.5)
            if long_order:
                return self._set(1)
            if short_order:
                return self._set(-1)
            return HOLD
        if family == "psar_ma_stable_reduce":
            middle = self._value(snapshot, "workbook_middle")
            psar = self._value(snapshot, "workbook_psar")
            direction = self._value(snapshot, "workbook_psar_direction")
            atr = self._value(snapshot, "workbook_atr")
            previous_direction = self._previous.get("psar_direction")
            self._previous["psar_direction"] = direction
            if None in (middle, psar, direction, atr, previous_direction):
                return HOLD
            if self.decision_position > 0 and previous_direction >= 0 > direction:
                return self._set(0)
            if self.decision_position < 0 and previous_direction <= 0 < direction:
                return self._set(0)
            far = abs(close - psar) > self.config.multiplier * atr
            far_active = bool(self._previous.get("psar_far_active", 0.0))
            self._previous["psar_far_active"] = float(far)
            if self.decision_position and far and not far_active:
                return self._set(self.decision_position * 0.5)
            stable_count = int(self._previous.get("psar_stable_count", 0.0))
            stable_count = stable_count + 1 if direction > 0 and close > middle else 0
            self._previous["psar_stable_count"] = float(stable_count)
            if stable_count >= self.config.consecutive_bars:
                return self._set(1)
            if direction < 0 and close < middle:
                return self._set(-1)
            return HOLD
        if family == "psar_atr_distance_exit":
            psar = self._value(snapshot, "workbook_psar")
            direction = self._value(snapshot, "workbook_psar_direction")
            atr = self._value(snapshot, "workbook_atr")
            previous_direction = self._previous.get("psar_direction")
            self._previous["psar_direction"] = direction
            if None in (psar, direction, atr, previous_direction) or atr <= 0:
                return HOLD
            if self.decision_position > 0 and previous_direction >= 0 > direction:
                return self._set(0)
            if self.decision_position < 0 and previous_direction <= 0 < direction:
                return self._set(0)
            pnl_atr = None
            if self.execution_entry_price is not None and self.decision_position:
                pnl_atr = self.decision_position / abs(self.decision_position) * (
                    close - self.execution_entry_price
                ) / atr
            if pnl_atr is not None and pnl_atr <= -self.config.stop_multiple:
                return self._set(0)
            if pnl_atr is not None and pnl_atr >= self.config.take_profit_multiple:
                stage = int(self._previous.get("psar_profit_stage", 0.0)) + 1
                self._previous["psar_profit_stage"] = float(stage)
                return self._set(self.decision_position * 0.5 if stage == 1 else 0)
            if not self.decision_position:
                self._previous["psar_profit_stage"] = 0.0
            if direction > 0 and close - psar > self.config.entry_distance_multiple * atr:
                return self._set(1)
            if direction < 0 and psar - close > self.config.entry_distance_multiple * atr:
                return self._set(-1)
            return HOLD
        if family == "ma_rsi_turn_filter":
            middle = self._value(snapshot, "workbook_middle")
            rsi = self._value(snapshot, "workbook_rsi")
            previous_rsi = self._previous.get("rsi")
            older_rsi = self._previous.get("rsi_older")
            self._previous.update(rsi_older=previous_rsi, rsi=rsi)
            if None in (middle, rsi, previous_rsi, older_rsi):
                return HOLD
            if self.decision_position > 0 and (
                close < middle or rsi >= self.config.exit_upper_threshold
            ):
                return self._set(0)
            if self.decision_position < 0 and (
                close > middle or rsi <= self.config.exit_lower_threshold
            ):
                return self._set(0)
            above_count = int(self._previous.get("ma_above_count", 0.0))
            above_count = above_count + 1 if close > middle else 0
            self._previous["ma_above_count"] = float(above_count)
            below_count = int(self._previous.get("ma_below_count", 0.0))
            below_count = below_count + 1 if close < middle else 0
            self._previous["ma_below_count"] = float(below_count)
            if (
                above_count >= self.config.consecutive_bars
                and previous_rsi <= self.config.lower_threshold
                and turn_up(older_rsi, previous_rsi, rsi)
            ):
                return self._set(1)
            if (
                below_count >= self.config.consecutive_bars
                and previous_rsi >= self.config.upper_threshold
                and turn_down(older_rsi, previous_rsi, rsi)
            ):
                return self._set(-1)
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
        if family in {"atr_channel", "atr_channel_confirmed"}:
            middle, atr = self._value(snapshot, "workbook_middle"), self._value(snapshot, "workbook_atr")
            if middle is None or atr is None:
                return HOLD
            if self.decision_position > 0 and close <= middle:
                return self._set(0)
            if self.decision_position < 0 and close >= middle:
                return self._set(0)
            upper, lower = middle + self.config.multiplier * atr, middle - self.config.multiplier * atr
            if family == "atr_channel_confirmed":
                direction = 1 if close > upper else -1 if close < lower else 0
                previous_direction = int(self._previous.get("extreme_direction", 0))
                self._extreme_count = self._extreme_count + 1 if direction and direction == previous_direction else (1 if direction else 0)
                self._previous["extreme_direction"] = float(direction)
                if self._extreme_count >= self.config.consecutive_bars:
                    return self._set(direction)
                return HOLD
            previous_close = self._previous.get("close")
            previous_upper, previous_lower = self._previous.get("upper"), self._previous.get("lower")
            self._previous.update(close=close, upper=upper, lower=lower)
            if previous_close is not None and previous_upper is not None and previous_close <= previous_upper and close > upper:
                return self._set(1)
            if previous_close is not None and previous_lower is not None and previous_close >= previous_lower and close < lower:
                return self._set(-1)
            return HOLD
        if family == "triple_sma":
            fast = self._value(snapshot, "workbook_fast")
            middle = self._value(snapshot, "workbook_middle_ma")
            slow = self._value(snapshot, "workbook_slow")
            previous_fast = self._previous.get("fast")
            previous_middle = self._previous.get("middle_ma")
            previous_slow = self._previous.get("slow")
            self._previous.update(fast=fast, middle_ma=middle, slow=slow)
            if None in (fast, middle, slow, previous_fast, previous_middle, previous_slow):
                return HOLD
            # Source row 25 enters only when the full ordering is present and
            # the fast/middle crossover occurs on this completed bar.
            if previous_fast <= previous_middle and fast > middle and fast > middle > slow:
                return self._set(1)
            if previous_fast >= previous_middle and fast < middle and fast < middle < slow:
                return self._set(-1)
            if self.decision_position > 0 and (fast < slow or not fast > middle > slow):
                return self._set(0)
            if self.decision_position < 0 and (fast > slow or not fast < middle < slow):
                return self._set(0)
            return HOLD
        if family == "hma_turn":
            hma = self._value(snapshot, "workbook_hma")
            previous = self._previous.get("hma")
            older = self._previous.get("hma_older")
            self._previous.update(hma_older=previous, hma=hma)
            if None in (hma, previous, older):
                return HOLD
            turns_up = previous <= older and hma > previous
            turns_down = previous >= older and hma < previous
            if turns_up and close > hma:
                return self._set(1)
            if turns_down and close < hma:
                return self._set(-1)
            if (self.decision_position > 0 and turns_down) or (self.decision_position < 0 and turns_up):
                return self._set(0)
            return HOLD
        if family == "cci_ma":
            cci = self._value(snapshot, "workbook_cci")
            middle = self._value(snapshot, "workbook_middle")
            previous_cci = self._previous.get("cci")
            self._previous["cci"] = cci
            if None in (cci, middle, previous_cci):
                return HOLD
            if self.decision_position > 0 and (
                (previous_cci <= 100 < cci) or close < middle
            ):
                return self._set(0)
            if self.decision_position < 0 and (
                (previous_cci >= -100 > cci) or close > middle
            ):
                return self._set(0)
            if previous_cci <= -100 < cci and close > middle:
                return self._set(1)
            if previous_cci >= 100 > cci and close < middle:
                return self._set(-1)
            return HOLD
        if family == "hlc_mean_cross_confirmed":
            middle = self._value(snapshot, "workbook_hlc_mean")
            if middle is None:
                return HOLD
            if self.decision_position > 0 and close < middle:
                return self._set(0)
            if self.decision_position < 0 and close > middle:
                return self._set(0)
            direction = 1 if close > middle else -1 if close < middle else 0
            previous_direction = int(self._previous.get("extreme_direction", 0))
            self._extreme_count = self._extreme_count + 1 if direction and direction == previous_direction else (1 if direction else 0)
            self._previous["extreme_direction"] = float(direction)
            if self._extreme_count >= self.config.consecutive_bars:
                return self._set(direction)
            return HOLD
        if family in {"adx_donchian", "adx_di_donchian", "adx_di_cross_donchian", "adx_di_recent_extreme"}:
            adx = self._value(snapshot, "workbook_adx")
            entry_up = self._value(snapshot, "workbook_entry_up")
            entry_down = self._value(snapshot, "workbook_entry_down")
            exit_up = self._value(snapshot, "workbook_exit_up")
            exit_down = self._value(snapshot, "workbook_exit_down")
            if None in (adx, entry_up, entry_down, exit_up, exit_down):
                return HOLD
            plus_di = minus_di = None
            if family in {"adx_di_donchian", "adx_di_cross_donchian", "adx_di_recent_extreme"}:
                plus_di = self._value(snapshot, "workbook_plus_di")
                minus_di = self._value(snapshot, "workbook_minus_di")
                if None in (plus_di, minus_di):
                    return HOLD
            previous_plus = self._previous.get("plus_di")
            previous_minus = self._previous.get("minus_di")
            if plus_di is not None and minus_di is not None:
                self._previous.update(plus_di=plus_di, minus_di=minus_di)
            if self.decision_position > 0 and (
                adx < self.config.adx_exit_threshold
                or (family != "adx_di_recent_extreme" and bool(exit_down))
                or (plus_di is not None and minus_di is not None and plus_di < minus_di)
            ):
                return self._set(0)
            if self.decision_position < 0 and (
                adx < self.config.adx_exit_threshold
                or (family != "adx_di_recent_extreme" and bool(exit_up))
                or (plus_di is not None and minus_di is not None and minus_di < plus_di)
            ):
                return self._set(0)
            if adx > self.config.adx_entry_threshold:
                long_di = plus_di is None or plus_di > minus_di
                short_di = minus_di is None or minus_di > plus_di
                if family in {"adx_di_cross_donchian", "adx_di_recent_extreme"}:
                    if None in (previous_plus, previous_minus):
                        return HOLD
                    long_di = cross_above(previous_plus, previous_minus, plus_di, minus_di)
                    short_di = cross_below(previous_plus, previous_minus, plus_di, minus_di)
                if bool(entry_up) and long_di:
                    return self._set(1)
                if bool(entry_down) and short_di:
                    return self._set(-1)
            return HOLD
        if family == "ao_breakout":
            ao = self._value(snapshot, "workbook_ao")
            entry_up = self._value(snapshot, "workbook_entry_up")
            entry_down = self._value(snapshot, "workbook_entry_down")
            previous = self._previous.get("ao")
            older = self._previous.get("ao_older")
            cross_age = int(self._previous.get("ao_cross_age", 99)) + 1
            if None in (ao, entry_up, entry_down):
                return HOLD
            if previous is not None:
                if previous <= 0 < ao:
                    self._previous["ao_cross_direction"] = 1.0; cross_age = 0
                elif previous >= 0 > ao:
                    self._previous["ao_cross_direction"] = -1.0; cross_age = 0
            direction = int(self._previous.get("ao_cross_direction", 0))
            slope = 1 if previous is not None and ao > previous else -1 if previous is not None and ao < previous else 0
            previous_slope = int(self._previous.get("ao_slope", 0))
            self._previous.update(ao_older=previous, ao=ao, ao_cross_age=float(cross_age), ao_slope=float(slope))
            if self.decision_position > 0 and (ao < 0 or (previous_slope > 0 and slope < 0)):
                return self._set(0)
            if self.decision_position < 0 and (ao > 0 or (previous_slope < 0 and slope > 0)):
                return self._set(0)
            two_green = older is not None and previous is not None and older < previous < ao
            two_red = older is not None and previous is not None and older > previous > ao
            if direction == 1 and cross_age <= 1 and two_green and bool(entry_up):
                return self._set(1)
            if direction == -1 and cross_age <= 1 and two_red and bool(entry_down):
                return self._set(-1)
            return HOLD
        if family in {"aroon_trend", "aroon_oscillator"}:
            up = self._value(snapshot, "workbook_aroon_up")
            down = self._value(snapshot, "workbook_aroon_down")
            osc = self._value(snapshot, "workbook_aroon_osc")
            previous_up = self._previous.get("aroon_up")
            previous_down = self._previous.get("aroon_down")
            previous_osc = self._previous.get("aroon_osc")
            self._previous.update(aroon_up=up, aroon_down=down, aroon_osc=osc)
            if None in (up, down, osc, previous_up, previous_down, previous_osc):
                return HOLD
            if family == "aroon_trend":
                if self.decision_position > 0 and (cross_below(previous_up, 30.0, up, 30.0) or cross_below(previous_up, previous_down, up, down)):
                    return self._set(0)
                if self.decision_position < 0 and (cross_below(previous_down, 30.0, down, 30.0) or cross_below(previous_down, previous_up, down, up)):
                    return self._set(0)
                if cross_above(previous_up, 70.0, up, 70.0) and up > down:
                    return self._set(1)
                if cross_above(previous_down, 70.0, down, 70.0) and down > up:
                    return self._set(-1)
            else:
                if self.decision_position > 0 and (cross_below(previous_osc, -20.0, osc, -20.0) or cross_below(previous_osc, 0.0, osc, 0.0)):
                    return self._set(0)
                if self.decision_position < 0 and (cross_above(previous_osc, 20.0, osc, 20.0) or cross_above(previous_osc, 0.0, osc, 0.0)):
                    return self._set(0)
                if cross_above(previous_osc, 0.0, osc, 0.0) and osc > 30.0:
                    return self._set(1)
                if cross_below(previous_osc, 0.0, osc, 0.0) and osc < -30.0:
                    return self._set(-1)
            return HOLD
        if family == "psar_reversal":
            direction = self._value(snapshot, "workbook_psar_direction")
            previous_direction = self._previous.get("psar_direction")
            self._previous["psar_direction"] = direction
            if direction is None or previous_direction is None:
                return HOLD
            if previous_direction <= 0 < direction:
                return self._set(1)
            if previous_direction >= 0 > direction:
                return self._set(-1)
            return HOLD
        if family == "fractal_ma_breakout":
            middle = self._value(snapshot, "workbook_middle")
            upper = self._value(snapshot, "workbook_upper_fractal")
            lower = self._value(snapshot, "workbook_lower_fractal")
            previous_close = self._previous.get("close")
            previous_middle = self._previous.get("middle")
            previous_upper = self._previous.get("upper_fractal")
            previous_lower = self._previous.get("lower_fractal")
            self._previous.update(close=close, middle=middle, upper_fractal=upper, lower_fractal=lower)
            if None in (middle, upper, lower, previous_close, previous_middle, previous_upper, previous_lower):
                return HOLD
            if self.decision_position > 0 and close < lower:
                return self._set(0)
            if self.decision_position < 0 and close > upper:
                return self._set(0)
            if middle > previous_middle and cross_above(previous_close, previous_upper, close, upper):
                return self._set(1)
            if middle < previous_middle and cross_below(previous_close, previous_lower, close, lower):
                return self._set(-1)
            return HOLD
        if family == "fractal_adx":
            middle = self._value(snapshot, "workbook_middle")
            adx = self._value(snapshot, "workbook_adx")
            upper = self._value(snapshot, "workbook_upper_pulse")
            lower = self._value(snapshot, "workbook_lower_pulse")
            if None in (middle, adx, upper, lower):
                return HOLD
            if self.decision_position > 0 and (bool(upper) or adx < self.config.adx_exit_threshold):
                return self._set(0)
            if self.decision_position < 0 and (bool(lower) or adx < self.config.adx_exit_threshold):
                return self._set(0)
            if adx > self.config.adx_entry_threshold and bool(lower) and close > middle:
                return self._set(1)
            if adx > self.config.adx_entry_threshold and bool(upper) and close < middle:
                return self._set(-1)
            return HOLD
        if family == "fractal_adx_stable":
            middle = self._value(snapshot, "workbook_middle")
            adx = self._value(snapshot, "workbook_adx")
            upper = self._value(snapshot, "workbook_upper_pulse")
            lower = self._value(snapshot, "workbook_lower_pulse")
            if None in (middle, adx, upper, lower):
                return HOLD
            if self.decision_position > 0 and (bool(upper) or adx < self.config.adx_exit_threshold):
                return self._set(0)
            if self.decision_position < 0 and (bool(lower) or adx < self.config.adx_exit_threshold):
                return self._set(0)
            above = int(self._previous.get("fractal_above_count", 0))
            below = int(self._previous.get("fractal_below_count", 0))
            self._previous["fractal_above_count"] = float(above + 1 if close > middle else 0)
            self._previous["fractal_below_count"] = float(below + 1 if close < middle else 0)
            if adx > self.config.adx_entry_threshold and bool(lower) and self._previous["fractal_above_count"] >= self.config.consecutive_bars:
                return self._set(1)
            if adx > self.config.adx_entry_threshold and bool(upper) and self._previous["fractal_below_count"] >= self.config.consecutive_bars:
                return self._set(-1)
            return HOLD
        if family == "cci_touch_reduce":
            cci = self._value(snapshot, "workbook_cci")
            middle = self._value(snapshot, "workbook_middle")
            previous = self._previous.get("cci")
            self._previous["cci"] = cci
            if None in (cci, middle, previous):
                return HOLD
            if self.decision_position > 0 and previous < 100 <= cci:
                return self._set(0)
            if self.decision_position < 0 and previous > -100 >= cci:
                return self._set(0)
            if self.decision_position and previous * cci <= 0:
                return self._set(self.decision_position * 0.5)
            if close > middle and previous <= -100 < cci:
                return self._set(1)
            if close < middle and previous >= 100 > cci:
                return self._set(-1)
            return HOLD
        if family == "donchian_pyramid":
            atr = self._value(snapshot, "workbook_atr")
            trend_up = self._value(snapshot, "workbook_trend_up")
            trend_down = self._value(snapshot, "workbook_trend_down")
            entry_up = self._value(snapshot, "workbook_entry_up")
            entry_down = self._value(snapshot, "workbook_entry_down")
            exit_up = self._value(snapshot, "workbook_exit_up")
            exit_down = self._value(snapshot, "workbook_exit_down")
            if None in (atr, trend_up, trend_down, entry_up, entry_down, exit_up, exit_down):
                return HOLD
            if atr <= 0:
                return HOLD
            if bool(trend_up):
                self._previous["channel_direction"] = 1.0
            elif bool(trend_down):
                self._previous["channel_direction"] = -1.0
            if self.decision_position > 0 and (bool(exit_down) or (
                self.execution_entry_price is not None and close <= self.execution_entry_price - self.config.stop_multiple * atr
            )):
                return self._set(0)
            if self.decision_position < 0 and (bool(exit_up) or (
                self.execution_entry_price is not None and close >= self.execution_entry_price + self.config.stop_multiple * atr
            )):
                return self._set(0)
            assert self._grid is not None
            if self.decision_position:
                target = self._grid.add_target(
                    price=close, atr=atr,
                    direction=PyramidDirection(self.config.pyramid_direction),
                )
                return self._set(target) if target is not None else HOLD
            channel_direction = int(self._previous.get("channel_direction", 0))
            if channel_direction > 0 and bool(entry_up):
                return self._set(self._grid.initial_target(1))
            if channel_direction < 0 and bool(entry_down):
                return self._set(self._grid.initial_target(-1))
            return HOLD
        if family == "sma_donchian_trend":
            middle = self._value(snapshot, "workbook_middle")
            entry_up = self._value(snapshot, "workbook_entry_up")
            entry_down = self._value(snapshot, "workbook_entry_down")
            exit_up = self._value(snapshot, "workbook_exit_up")
            exit_down = self._value(snapshot, "workbook_exit_down")
            previous_middle = self._previous.get("middle")
            self._previous["middle"] = middle
            if None in (middle, previous_middle, entry_up, entry_down, exit_up, exit_down):
                return HOLD
            if self.decision_position > 0 and (bool(exit_down) or middle < previous_middle):
                return self._set(0)
            if self.decision_position < 0 and (bool(exit_up) or middle > previous_middle):
                return self._set(0)
            if middle > previous_middle and bool(entry_up):
                return self._set(1)
            if middle < previous_middle and bool(entry_down):
                return self._set(-1)
            return HOLD
        if family == "supertrend_stop":
            direction = self._value(snapshot, "workbook_supertrend_direction")
            atr = self._value(snapshot, "workbook_atr")
            previous = self._previous.get("supertrend_direction")
            self._previous["supertrend_direction"] = direction
            if None in (direction, atr, previous):
                return HOLD
            stop_hit = self.execution_entry_price is not None and (
                (self.decision_position > 0 and close <= self.execution_entry_price - self.config.stop_multiple * atr)
                or (self.decision_position < 0 and close >= self.execution_entry_price + self.config.stop_multiple * atr)
            )
            if stop_hit:
                return self._set(0)
            if previous <= 0 < direction:
                return self._set(1)
            if previous >= 0 > direction:
                return self._set(-1)
            return HOLD
        if family in {"donchian_ma_stop", "adx_donchian_stop", "donchian_stop"}:
            atr = self._value(snapshot, "workbook_atr")
            entry_up = self._value(snapshot, "workbook_entry_up")
            entry_down = self._value(snapshot, "workbook_entry_down")
            exit_up = self._value(snapshot, "workbook_exit_up")
            exit_down = self._value(snapshot, "workbook_exit_down")
            if None in (atr, entry_up, entry_down, exit_up, exit_down):
                return HOLD
            stop_hit = False
            if self.execution_entry_price is not None:
                stop_hit = (
                    self.decision_position > 0 and close <= self.execution_entry_price - self.config.stop_multiple * atr
                ) or (
                    self.decision_position < 0 and close >= self.execution_entry_price + self.config.stop_multiple * atr
                )
            adx = self._value(snapshot, "workbook_adx") if family == "adx_donchian_stop" else None
            middle = self._value(snapshot, "workbook_middle") if family == "donchian_ma_stop" else None
            if self.decision_position > 0 and (bool(exit_down) or stop_hit or (adx is not None and adx < self.config.adx_exit_threshold)):
                return self._set(0)
            if self.decision_position < 0 and (bool(exit_up) or stop_hit or (adx is not None and adx < self.config.adx_exit_threshold)):
                return self._set(0)
            allowed = adx > self.config.adx_entry_threshold if adx is not None else True
            if middle is not None:
                previous_middle = self._previous.get("middle")
                self._previous["middle"] = middle
                if previous_middle is None:
                    return HOLD
                if allowed and bool(entry_up) and middle > previous_middle:
                    return self._set(1)
                if allowed and bool(entry_down) and middle < previous_middle:
                    return self._set(-1)
            elif allowed:
                if bool(entry_up): return self._set(1)
                if bool(entry_down): return self._set(-1)
            return HOLD
        if family in {"adx_sma_take_profit", "ema_adx_take_profit"}:
            fast = self._value(snapshot, "workbook_fast")
            slow = self._value(snapshot, "workbook_slow")
            adx = self._value(snapshot, "workbook_adx")
            atr = self._value(snapshot, "workbook_atr")
            previous_fast, previous_slow = self._previous.get("fast"), self._previous.get("slow")
            self._previous.update(fast=fast, slow=slow)
            if None in (fast, slow, adx, atr, previous_fast, previous_slow):
                return HOLD
            plus_di = minus_di = None
            if family == "ema_adx_take_profit":
                plus_di = self._value(snapshot, "workbook_plus_di")
                minus_di = self._value(snapshot, "workbook_minus_di")
                if None in (plus_di, minus_di): return HOLD
            profit_hit = False
            if self.execution_entry_price is not None:
                profit_hit = (
                    self.decision_position > 0 and close >= self.execution_entry_price + self.config.take_profit_multiple * atr
                ) or (
                    self.decision_position < 0 and close <= self.execution_entry_price - self.config.take_profit_multiple * atr
                )
            opposite = (
                self.decision_position > 0 and cross_below(previous_fast, previous_slow, fast, slow)
            ) or (
                self.decision_position < 0 and cross_above(previous_fast, previous_slow, fast, slow)
            )
            if self.decision_position and (adx < self.config.adx_exit_threshold or opposite or profit_hit):
                return self._set(0)
            if adx > self.config.adx_entry_threshold:
                if cross_above(previous_fast, previous_slow, fast, slow) and close > fast and close > slow and (plus_di is None or plus_di > minus_di):
                    return self._set(1)
                if cross_below(previous_fast, previous_slow, fast, slow) and close < fast and close < slow and (minus_di is None or minus_di > plus_di):
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
        elif self.config.family == "bollinger_width_cross":
            fast = self._value(snapshot, "workbook_bbw_fast")
            slow = self._value(snapshot, "workbook_bbw_slow")
            if fast is not None and slow is not None:
                self._previous.update(bbw_fast=fast, bbw_slow=slow)
        elif self.config.family == "sma_price_cross":
            middle = self._value(snapshot, "workbook_middle")
            if middle is not None and close is not None:
                self._previous.update(close=close, middle=middle)
        elif self.config.family == "ema_crossover":
            fast = self._value(snapshot, "workbook_fast")
            slow = self._value(snapshot, "workbook_slow")
            if fast is not None and slow is not None:
                self._previous.update(fast=fast, slow=slow)
        elif self.config.family == "ma_cross_slope_atr_exit":
            fast = self._value(snapshot, "workbook_fast")
            slow = self._value(snapshot, "workbook_slow")
            if fast is not None and slow is not None:
                previous_fast, previous_slow = self._previous.get("fast"), self._previous.get("slow")
                self._previous.update(
                    fast_older=previous_fast, slow_older=previous_slow, fast=fast, slow=slow,
                )
        elif self.config.family == "rsi_turn_candle":
            rsi = self._value(snapshot, "workbook_rsi")
            if rsi is not None:
                previous = self._previous.get("rsi")
                self._previous.update(rsi_older=previous, rsi=rsi)
        elif self.config.family == "macd_zero_trend":
            dif = self._value(snapshot, "workbook_macd_dif")
            signal_line = self._value(snapshot, "workbook_macd_signal")
            histogram = self._value(snapshot, "workbook_macd_histogram")
            if None not in (dif, signal_line, histogram):
                previous_hist = self._previous.get("macd_hist")
                self._previous.update(
                    macd_dif=dif, macd_signal=signal_line,
                    macd_hist_older=previous_hist, macd_hist=histogram,
                )
        elif self.config.family == "four_ma_stable_layered":
            filter_ma = self._value(snapshot, "workbook_filter")
            if filter_ma is not None and close is not None:
                previous_filter = self._previous.get("filter")
                self._previous.update(close=close, filter_older=previous_filter, filter=filter_ma)
        elif self.config.family == "psar_ma_stable_reduce":
            direction = self._value(snapshot, "workbook_psar_direction")
            if direction is not None:
                self._previous["psar_direction"] = direction
        elif self.config.family == "psar_atr_distance_exit":
            direction = self._value(snapshot, "workbook_psar_direction")
            if direction is not None:
                self._previous["psar_direction"] = direction
        elif self.config.family == "ma_rsi_turn_filter":
            rsi = self._value(snapshot, "workbook_rsi")
            if rsi is not None:
                previous = self._previous.get("rsi")
                self._previous.update(rsi_older=previous, rsi=rsi)
        elif self.config.family == "ma_envelope":
            middle = self._value(snapshot, "workbook_middle")
            if middle is not None:
                self._previous["middle"] = middle
        elif self.config.family in {"atr_channel", "atr_channel_confirmed"}:
            middle = self._value(snapshot, "workbook_middle")
            atr = self._value(snapshot, "workbook_atr")
            if close is not None and middle is not None and atr is not None:
                self._previous.update(
                    upper=middle + self.config.multiplier * atr,
                    lower=middle - self.config.multiplier * atr,
                )
        elif self.config.family == "triple_sma":
            fast = self._value(snapshot, "workbook_fast")
            middle = self._value(snapshot, "workbook_middle_ma")
            slow = self._value(snapshot, "workbook_slow")
            if None not in (fast, middle, slow):
                self._previous.update(fast=fast, middle_ma=middle, slow=slow)
        elif self.config.family == "hma_turn":
            hma = self._value(snapshot, "workbook_hma")
            previous = self._previous.get("hma")
            if hma is not None:
                self._previous.update(hma_older=previous, hma=hma)
        elif self.config.family == "cci_ma":
            cci = self._value(snapshot, "workbook_cci")
            if cci is not None:
                self._previous["cci"] = cci
        elif self.config.family == "hlc_mean_cross_confirmed":
            middle = self._value(snapshot, "workbook_hlc_mean")
            if close is not None and middle is not None:
                direction = 1 if close > middle else -1 if close < middle else 0
                previous_direction = int(self._previous.get("extreme_direction", 0))
                self._extreme_count = self._extreme_count + 1 if direction and direction == previous_direction else (1 if direction else 0)
                self._previous["extreme_direction"] = float(direction)
        elif self.config.family == "bollinger":
            percent_b = self._value(snapshot, "workbook_percent_b")
            if percent_b is not None:
                direction = 1 if percent_b > 1 else -1 if percent_b < 0 else 0
                previous_direction = int(self._previous.get("extreme_direction", 0))
                self._extreme_count = self._extreme_count + 1 if direction and direction == previous_direction else (1 if direction else 0)
                self._previous["extreme_direction"] = float(direction)
        elif self.config.family == "ao_breakout":
            ao = self._value(snapshot, "workbook_ao")
            if ao is not None:
                previous = self._previous.get("ao")
                slope = 1 if previous is not None and ao > previous else -1 if previous is not None and ao < previous else 0
                self._previous.update(ao_older=previous, ao=ao, ao_slope=float(slope))
        elif self.config.family in {"aroon_trend", "aroon_oscillator"}:
            up = self._value(snapshot, "workbook_aroon_up")
            down = self._value(snapshot, "workbook_aroon_down")
            osc = self._value(snapshot, "workbook_aroon_osc")
            if None not in (up, down, osc):
                self._previous.update(aroon_up=up, aroon_down=down, aroon_osc=osc)
        elif self.config.family == "psar_reversal":
            direction = self._value(snapshot, "workbook_psar_direction")
            if direction is not None:
                self._previous["psar_direction"] = direction
        elif self.config.family == "fractal_ma_breakout":
            middle = self._value(snapshot, "workbook_middle")
            upper = self._value(snapshot, "workbook_upper_fractal")
            lower = self._value(snapshot, "workbook_lower_fractal")
            if None not in (middle, upper, lower) and close is not None:
                self._previous.update(close=close, middle=middle, upper_fractal=upper, lower_fractal=lower)
        elif self.config.family in {"fractal_adx", "sma_donchian_trend"}:
            middle = self._value(snapshot, "workbook_middle")
            if middle is not None:
                self._previous["middle"] = middle
        elif self.config.family == "supertrend_stop":
            direction = self._value(snapshot, "workbook_supertrend_direction")
            if direction is not None:
                self._previous["supertrend_direction"] = direction
