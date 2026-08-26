"""Composable, source-independent strategy risk/exit module contracts."""

from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field
from enum import Enum
from typing import Literal
from typing import Mapping
from typing import Protocol
from typing import runtime_checkable


DAY_NS = 86_400_000_000_000


@dataclass(frozen=True)
class ModuleContext:
    side: int
    entry_price: float
    current_price: float
    atr: float
    initial_exposure: float = 1.0
    upper_channel: float | None = None
    lower_channel: float | None = None
    adx: float | None = None
    event_time_ns: int | None = None
    current_exposure: float | None = None
    bars_held: int = 0
    highest_price_since_entry: float | None = None
    lowest_price_since_entry: float | None = None
    volatility_percentile: float | None = None
    account_drawdown: float | None = None
    feature_values: Mapping[str, float | bool] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.side not in (-1, 1):
            raise ValueError("side must be -1 or 1")
        if self.entry_price <= 0 or self.current_price <= 0 or self.atr <= 0:
            raise ValueError("prices and ATR must be positive")
        if not 0 < self.initial_exposure <= 1:
            raise ValueError("initial_exposure must be in (0, 1]")
        if self.adx is not None and self.adx < 0:
            raise ValueError("adx cannot be negative")
        if self.bars_held < 0:
            raise ValueError("bars_held cannot be negative")
        if self.volatility_percentile is not None and not 0 <= self.volatility_percentile <= 100:
            raise ValueError("volatility_percentile must be in [0, 100]")


@dataclass(frozen=True)
class ModuleDecision:
    target_exposure: float
    reason: str


@runtime_checkable
class StrategyModule(Protocol):
    module_id: str

    def evaluate(self, context: ModuleContext) -> ModuleDecision: ...


@dataclass(frozen=True)
class AtrLadderExitModule:
    """
    Deterministic ATR profit ladder plus hard stop.

    Reduction fractions are fractions of original exposure, matching workbook
    wording such as "减 20%".  The returned exposure is a target; execution and
    fill ownership remain in the normal execution layer.
    """

    module_id: str
    profit_levels_atr: tuple[float, ...]
    reduction_fractions: tuple[float, ...]
    final_profit_atr: float
    stop_loss_atr: float

    def __post_init__(self) -> None:
        if len(self.profit_levels_atr) != len(self.reduction_fractions):
            raise ValueError("profit levels and reduction fractions must have equal length")
        if tuple(sorted(self.profit_levels_atr)) != self.profit_levels_atr:
            raise ValueError("profit levels must be increasing")
        if any(level <= 0 for level in self.profit_levels_atr):
            raise ValueError("profit levels must be positive")
        if any(not 0 < fraction < 1 for fraction in self.reduction_fractions):
            raise ValueError("reduction fractions must be in (0, 1)")
        if sum(self.reduction_fractions) >= 1.0 + 1e-12:
            raise ValueError("partial reductions cannot exceed original exposure")
        if self.final_profit_atr <= (self.profit_levels_atr[-1] if self.profit_levels_atr else 0):
            raise ValueError("final profit level must follow all partial levels")
        if self.stop_loss_atr <= 0:
            raise ValueError("stop loss must be positive")

    def evaluate(self, context: ModuleContext) -> ModuleDecision:
        pnl_atr = context.side * (context.current_price - context.entry_price) / context.atr
        if pnl_atr <= -self.stop_loss_atr:
            return ModuleDecision(0.0, "atr_hard_stop")
        if pnl_atr >= self.final_profit_atr:
            return ModuleDecision(0.0, "atr_final_take_profit")
        reduced = sum(
            fraction
            for level, fraction in zip(self.profit_levels_atr, self.reduction_fractions)
            if pnl_atr >= level
        )
        target = context.initial_exposure * max(0.0, 1.0 - reduced)
        return ModuleDecision(target, "atr_ladder_reduce" if reduced else "hold")


@dataclass(frozen=True)
class AtrHardStopModule:
    """Flatten only when fill-anchored loss reaches an explicit ATR multiple."""

    module_id: str
    stop_loss_atr: float

    def __post_init__(self) -> None:
        if self.stop_loss_atr <= 0:
            raise ValueError("stop_loss_atr must be positive")

    def evaluate(self, context: ModuleContext) -> ModuleDecision:
        pnl_atr = context.side * (context.current_price - context.entry_price) / context.atr
        if pnl_atr <= -self.stop_loss_atr:
            return ModuleDecision(0.0, "atr_hard_stop")
        return ModuleDecision(context.initial_exposure, "hold")


@dataclass(frozen=True)
class DonchianExitModule:
    """Fill-independent channel exit using already-computed feature levels."""

    module_id: str
    window: int

    def __post_init__(self) -> None:
        if self.window <= 0:
            raise ValueError("window must be positive")

    def evaluate(self, context: ModuleContext) -> ModuleDecision:
        if context.upper_channel is None or context.lower_channel is None:
            raise ValueError("Donchian exit requires upper_channel and lower_channel")
        hit = (context.side > 0 and context.current_price < context.lower_channel) or (
            context.side < 0 and context.current_price > context.upper_channel
        )
        return (
            ModuleDecision(0.0, "donchian_exit")
            if hit
            else ModuleDecision(context.initial_exposure, "hold")
        )


@dataclass(frozen=True)
class AdxExposureModule:
    """Map explicit ADX regimes to exposure without owning execution state."""

    module_id: str
    full_threshold: float
    medium_threshold: float
    medium_exposure: float
    low_exposure: float

    def __post_init__(self) -> None:
        if self.full_threshold <= self.medium_threshold or self.medium_threshold < 0:
            raise ValueError("ADX thresholds must satisfy full > medium >= 0")
        if not 0 <= self.low_exposure <= self.medium_exposure <= 1:
            raise ValueError("ADX exposures must satisfy 0 <= low <= medium <= 1")

    def evaluate(self, context: ModuleContext) -> ModuleDecision:
        if context.adx is None:
            raise ValueError("ADX exposure module requires adx")
        multiplier = (
            1.0
            if context.adx > self.full_threshold
            else (
                self.medium_exposure if context.adx >= self.medium_threshold else self.low_exposure
            )
        )
        return ModuleDecision(context.initial_exposure * multiplier, "adx_exposure_regime")


@dataclass(frozen=True)
class FixedPercentageStopModule:
    """Fill-anchored symmetric percentage stop; no alpha or execution ownership."""

    module_id: str
    stop_fraction: float

    def __post_init__(self) -> None:
        if not 0 < self.stop_fraction < 1:
            raise ValueError("stop_fraction must be in (0, 1)")

    def evaluate(self, context: ModuleContext) -> ModuleDecision:
        loss = -context.side * (context.current_price / context.entry_price - 1.0)
        return (
            ModuleDecision(0.0, "fixed_percentage_stop")
            if loss + 1e-12 >= self.stop_fraction
            else ModuleDecision(context.initial_exposure, "hold")
        )


@dataclass(frozen=True)
class TimeExitModule:
    """Exit after an explicit number of completed host bars."""

    module_id: str
    maximum_holding_bars: int

    def __post_init__(self) -> None:
        if self.maximum_holding_bars <= 0:
            raise ValueError("maximum_holding_bars must be positive")

    def evaluate(self, context: ModuleContext) -> ModuleDecision:
        return (
            ModuleDecision(0.0, "maximum_holding_bars")
            if context.bars_held >= self.maximum_holding_bars
            else ModuleDecision(context.initial_exposure, "hold")
        )


@dataclass(frozen=True)
class ExposureCapModule:
    """Clamp desired absolute exposure without creating a direction."""

    module_id: str
    max_abs_exposure: float

    def __post_init__(self) -> None:
        if not 0 < self.max_abs_exposure <= 1:
            raise ValueError("max_abs_exposure must be in (0, 1]")

    def evaluate(self, context: ModuleContext) -> ModuleDecision:
        return ModuleDecision(
            min(context.initial_exposure, self.max_abs_exposure),
            "exposure_cap",
        )


@dataclass(frozen=True)
class EntryExposureCapModule:
    """Cap a new entry while leaving an already-filled smaller episode intact."""

    module_id: str
    max_entry_exposure: float

    def __post_init__(self) -> None:
        if not 0 < self.max_entry_exposure <= 1:
            raise ValueError("max_entry_exposure must be in (0, 1]")

    def evaluate(self, context: ModuleContext) -> ModuleDecision:
        current = abs(context.current_exposure or 0.0)
        target = (
            current if current > 1e-12 else min(context.initial_exposure, self.max_entry_exposure)
        )
        return ModuleDecision(target, "entry_exposure_cap")


@dataclass(frozen=True)
class VolatilityExposureModule:
    """Map an already-computed volatility percentile to explicit exposure tiers."""

    module_id: str
    upper_bounds: tuple[float, ...]
    exposures: tuple[float, ...]
    prohibit_entry_at_or_above: float | None = None

    def __post_init__(self) -> None:
        if len(self.exposures) != len(self.upper_bounds) + 1:
            raise ValueError("volatility exposures require one more exposure than bounds")
        if tuple(sorted(self.upper_bounds)) != self.upper_bounds:
            raise ValueError("volatility percentile bounds must increase")
        if any(not 0 <= value <= 100 for value in self.upper_bounds):
            raise ValueError("volatility percentile bounds must be in [0, 100]")
        if any(not 0 <= value <= 1 for value in self.exposures):
            raise ValueError("volatility exposures must be in [0, 1]")

    def evaluate(self, context: ModuleContext) -> ModuleDecision:
        if context.volatility_percentile is None:
            raise ValueError("volatility exposure module requires volatility_percentile")
        percentile = context.volatility_percentile
        if (
            self.prohibit_entry_at_or_above is not None
            and percentile >= self.prohibit_entry_at_or_above
        ):
            current = abs(context.current_exposure or 0.0)
            return ModuleDecision(
                min(current, context.initial_exposure), "volatility_entry_prohibited"
            )
        index = next(
            (i for i, bound in enumerate(self.upper_bounds) if percentile < bound),
            len(self.upper_bounds),
        )
        return ModuleDecision(
            context.initial_exposure * self.exposures[index],
            "volatility_exposure_tier",
        )


@dataclass
class AtrBreakevenTrailingModule:
    """Fill-synchronized ATR hard stop and monotonic breakeven/trailing stop."""

    module_id: str
    activation_atr: float
    lock_atr: float
    hard_stop_atr: float
    trail_distance_atr: float | None = None
    episode_side: int = 0
    entry_price: float | None = None
    stop_price: float | None = None

    def __post_init__(self) -> None:
        if self.activation_atr <= 0 or self.hard_stop_atr <= 0 or self.lock_atr < 0:
            raise ValueError("invalid breakeven ATR parameters")
        if self.trail_distance_atr is not None and self.trail_distance_atr <= 0:
            raise ValueError("trail_distance_atr must be positive")

    def synchronize_fill(self, *, position: float, fill_price: float) -> None:
        if fill_price <= 0:
            raise ValueError("fill_price must be positive")
        side = 1 if position > 0 else -1 if position < 0 else 0
        if side == 0:
            self.episode_side = 0
            self.entry_price = None
            self.stop_price = None
        elif side != self.episode_side:
            self.episode_side = side
            self.entry_price = fill_price
            self.stop_price = None

    def evaluate(self, context: ModuleContext) -> ModuleDecision:
        entry = self.entry_price if self.entry_price is not None else context.entry_price
        side = self.episode_side or context.side
        pnl_atr = side * (context.current_price - entry) / context.atr
        if pnl_atr <= -self.hard_stop_atr:
            return ModuleDecision(0.0, "atr_hard_stop")
        if pnl_atr >= self.activation_atr:
            candidate = entry + side * self.lock_atr * context.atr
            if self.trail_distance_atr is not None:
                extreme = (
                    context.highest_price_since_entry
                    if side > 0
                    else context.lowest_price_since_entry
                )
                if extreme is not None:
                    candidate = extreme - side * self.trail_distance_atr * context.atr
            if self.stop_price is None:
                self.stop_price = candidate
            elif side > 0:
                self.stop_price = max(self.stop_price, candidate)
            else:
                self.stop_price = min(self.stop_price, candidate)
        hit = self.stop_price is not None and (
            (side > 0 and context.current_price <= self.stop_price)
            or (side < 0 and context.current_price >= self.stop_price)
        )
        return (
            ModuleDecision(0.0, "breakeven_trailing_stop")
            if hit
            else ModuleDecision(context.initial_exposure, "hold")
        )


@dataclass(frozen=True)
class AccountDrawdownControlModule:
    """Explicit account drawdown tiers; accounting remains execution-owned."""

    module_id: str
    reduce_at: float
    flatten_at: float
    reduced_exposure: float = 0.5

    def __post_init__(self) -> None:
        if not 0 < self.reduce_at < self.flatten_at < 1:
            raise ValueError("drawdown thresholds must satisfy 0 < reduce < flatten < 1")
        if not 0 <= self.reduced_exposure <= 1:
            raise ValueError("reduced_exposure must be in [0, 1]")

    def evaluate(self, context: ModuleContext) -> ModuleDecision:
        if context.account_drawdown is None:
            raise ValueError("drawdown module requires account_drawdown")
        drawdown = abs(min(0.0, context.account_drawdown))
        if drawdown >= self.flatten_at:
            return ModuleDecision(0.0, "account_drawdown_flatten")
        if drawdown >= self.reduce_at:
            return ModuleDecision(
                context.initial_exposure * self.reduced_exposure, "account_drawdown_reduce"
            )
        return ModuleDecision(context.initial_exposure, "hold")


@dataclass(frozen=True)
class AtrTakeProfitModule:
    """Symmetric fill-anchored ATR take-profit and optional hard stop."""

    module_id: str
    take_profit_atr: float
    stop_loss_atr: float | None = None

    def __post_init__(self) -> None:
        if self.take_profit_atr <= 0:
            raise ValueError("take_profit_atr must be positive")
        if self.stop_loss_atr is not None and self.stop_loss_atr <= 0:
            raise ValueError("stop_loss_atr must be positive")

    def evaluate(self, context: ModuleContext) -> ModuleDecision:
        pnl_atr = context.side * (context.current_price - context.entry_price) / context.atr
        if self.stop_loss_atr is not None and pnl_atr <= -self.stop_loss_atr:
            return ModuleDecision(0.0, "atr_hard_stop")
        if pnl_atr >= self.take_profit_atr:
            return ModuleDecision(0.0, "atr_take_profit")
        return ModuleDecision(context.initial_exposure, "hold")


@dataclass(frozen=True)
class AtrAdverseReductionModule:
    """
    Reduce exposure at explicit adverse ATR distances.

    ``target_fractions`` are fractions of the initial episode exposure remaining,
    not order quantities. This makes re-evaluation idempotent before a fill.
    """

    module_id: str
    loss_levels_atr: tuple[float, ...]
    target_fractions: tuple[float, ...]

    def __post_init__(self) -> None:
        if len(self.loss_levels_atr) != len(self.target_fractions) or not self.loss_levels_atr:
            raise ValueError("loss levels and targets must be non-empty and equal length")
        if tuple(sorted(self.loss_levels_atr)) != self.loss_levels_atr:
            raise ValueError("loss levels must increase")
        if any(level <= 0 for level in self.loss_levels_atr):
            raise ValueError("loss levels must be positive")
        if any(not 0 <= target <= 1 for target in self.target_fractions):
            raise ValueError("target fractions must be in [0, 1]")
        if any(a < b for a, b in zip(self.target_fractions, self.target_fractions[1:])):
            raise ValueError("remaining exposure cannot increase as losses deepen")

    def evaluate(self, context: ModuleContext) -> ModuleDecision:
        loss_atr = -context.side * (context.current_price - context.entry_price) / context.atr
        target = 1.0
        for level, remaining in zip(self.loss_levels_atr, self.target_fractions):
            if loss_atr >= level:
                target = remaining
        return ModuleDecision(
            context.initial_exposure * target,
            "atr_adverse_reduce" if target < 1.0 else "hold",
        )


@dataclass(frozen=True)
class FeatureExitCondition:
    """Typed condition over a canonical feature supplied by the host runner."""

    feature_key: str
    operator: Literal["true", "false", "gt", "ge", "lt", "le", "eq"] = "true"
    threshold: float | None = None
    side: Literal["both", "long", "short"] = "both"

    def __post_init__(self) -> None:
        if not self.feature_key:
            raise ValueError("feature_key is required")
        if self.operator not in {"true", "false"} and self.threshold is None:
            raise ValueError("numeric feature conditions require a threshold")

    def matches(self, context: ModuleContext) -> bool:
        if (self.side == "long" and context.side < 0) or (self.side == "short" and context.side > 0):
            return False
        if self.feature_key not in context.feature_values:
            raise ValueError(f"missing module feature {self.feature_key!r}")
        value = context.feature_values[self.feature_key]
        if self.operator == "true":
            return bool(value)
        if self.operator == "false":
            return not bool(value)
        numeric = float(value)
        threshold = float(self.threshold)
        return {
            "gt": numeric > threshold,
            "ge": numeric >= threshold,
            "lt": numeric < threshold,
            "le": numeric <= threshold,
            "eq": numeric == threshold,
        }[self.operator]


@dataclass(frozen=True)
class FeatureExitModule:
    """Flatten when any explicit typed feature condition is true."""

    module_id: str
    conditions: tuple[FeatureExitCondition, ...]

    def __post_init__(self) -> None:
        if not self.conditions:
            raise ValueError("at least one exit condition is required")

    def evaluate(self, context: ModuleContext) -> ModuleDecision:
        for condition in self.conditions:
            if condition.matches(context):
                return ModuleDecision(0.0, f"feature_exit:{condition.feature_key}")
        return ModuleDecision(context.initial_exposure, "hold")


@dataclass(frozen=True)
class FeatureExposureModule:
    """Apply an explicit exposure fraction when a typed feature condition holds."""

    module_id: str
    condition: FeatureExitCondition
    target_fraction: float

    def __post_init__(self) -> None:
        if not 0 <= self.target_fraction <= 1:
            raise ValueError("target_fraction must be in [0, 1]")

    def evaluate(self, context: ModuleContext) -> ModuleDecision:
        if self.condition.matches(context):
            return ModuleDecision(
                context.initial_exposure * self.target_fraction,
                f"feature_exposure:{self.condition.feature_key}",
            )
        return ModuleDecision(context.initial_exposure, "hold")


@dataclass
class DailyRiskControlModule:
    """UTC-session loss and executed-entry limits using fill/account feedback."""

    module_id: str
    maximum_loss: float | None = None
    maximum_entries: int | None = None
    state: SessionRiskState = field(default_factory=lambda: SessionRiskState())

    def __post_init__(self) -> None:
        if self.maximum_loss is None and self.maximum_entries is None:
            raise ValueError("at least one daily risk limit is required")
        if self.maximum_loss is not None and self.maximum_loss <= 0:
            raise ValueError("maximum_loss must be positive")
        if self.maximum_entries is not None and self.maximum_entries <= 0:
            raise ValueError("maximum_entries must be positive")

    def update_execution(
        self,
        *,
        event_time_ns: int,
        realized_pnl_delta: float = 0.0,
        unrealized_pnl: float = 0.0,
        executed_entry: bool = False,
    ) -> None:
        self.state.update(
            event_time_ns=event_time_ns,
            realized_pnl_delta=realized_pnl_delta,
            unrealized_pnl=unrealized_pnl,
            executed_entry=executed_entry,
        )

    def entry_allowed(self) -> bool:
        loss_hit = self.maximum_loss is not None and self.state.loss_limit_hit(self.maximum_loss)
        count_hit = self.maximum_entries is not None and self.state.entry_limit_hit(
            self.maximum_entries
        )
        return not (loss_hit or count_hit)

    def evaluate(self, context: ModuleContext) -> ModuleDecision:
        if self.entry_allowed():
            return ModuleDecision(context.initial_exposure, "daily_risk_clear")
        # A daily entry lock must not fabricate a close for an existing episode.
        current = abs(context.current_exposure or 0.0)
        return ModuleDecision(current, "daily_entry_locked")


@dataclass
class CompositeRiskModule:
    """
    Compose risk-reducing modules without introducing alpha intent.

    Child modules return absolute episode exposure targets. The most
    risk-reducing target wins, so an exit cannot be overwritten by a sizing or
    reduction module evaluated later in the same timestamp.
    """

    module_id: str
    modules: tuple[StrategyModule, ...]

    def __post_init__(self) -> None:
        if not self.modules:
            raise ValueError("composite module requires at least one child")

    def evaluate(self, context: ModuleContext) -> ModuleDecision:
        decisions = [module.evaluate(context) for module in self.modules]
        return min(decisions, key=lambda decision: (abs(decision.target_exposure), decision.reason))

    def synchronize_fill(self, *, position: float, fill_price: float) -> None:
        for module in self.modules:
            callback = getattr(module, "synchronize_fill", None)
            if callback is not None:
                callback(position=position, fill_price=fill_price)


class PyramidDirection(str, Enum):
    FAVORABLE = "favorable"
    ADVERSE = "adverse"


@dataclass
class GridPyramidState:
    """
    Fill-reconciled decision state for a finite grid/pyramid episode.

    It proposes exposure targets but does not execute orders. A proposal stays
    pending until a confirmed fill synchronizes the position, so repeated
    observations cannot append the same layer more than once.
    """

    layers: int = 4
    step_atr: float = 1.0
    max_abs_exposure: float = 1.0
    layer_fraction: float = 0.25
    layer_fractions: tuple[float, ...] | None = None
    episode_side: int = 0
    initial_entry_price: float | None = None
    latest_add_price: float | None = None
    grid_layer_index: int = 0
    current_exposure: float = 0.0
    next_add_trigger: float | None = None
    completed_reduction_levels: set[str] = field(default_factory=set)
    pending_target: float | None = None

    def __post_init__(self) -> None:
        if self.layers <= 0 or self.step_atr <= 0:
            raise ValueError("layers and step_atr must be positive")
        if not 0 < self.max_abs_exposure <= 1:
            raise ValueError("max_abs_exposure must be in (0, 1]")
        if not 0 < self.layer_fraction <= self.max_abs_exposure:
            raise ValueError("layer_fraction must be in (0, max_abs_exposure]")
        if self.layer_fractions is not None:
            if len(self.layer_fractions) != self.layers or any(value <= 0 for value in self.layer_fractions):
                raise ValueError("layer_fractions must contain one positive fraction per layer")
            if abs(sum(self.layer_fractions) - self.max_abs_exposure) > 1e-12:
                raise ValueError("layer_fractions must sum to max_abs_exposure")

    def _fraction_for_layer(self, zero_based_index: int) -> float:
        return (
            self.layer_fractions[zero_based_index]
            if self.layer_fractions is not None
            else self.layer_fraction
        )

    def _reset(self) -> None:
        self.episode_side = 0
        self.initial_entry_price = None
        self.latest_add_price = None
        self.grid_layer_index = 0
        self.current_exposure = 0.0
        self.next_add_trigger = None
        self.completed_reduction_levels.clear()
        self.pending_target = None

    def synchronize_fill(self, *, position: float, fill_price: float) -> None:
        """Synchronize state only from a confirmed execution fill."""
        if fill_price <= 0 or abs(position) > self.max_abs_exposure + 1e-12:
            raise ValueError("invalid fill price or exposure")
        position = float(position)
        if abs(position) <= 1e-12:
            self._reset()
            return
        side = 1 if position > 0 else -1
        previous_abs = abs(self.current_exposure)
        if self.episode_side != side:
            self.episode_side = side
            self.initial_entry_price = float(fill_price)
            self.grid_layer_index = 1
            self.completed_reduction_levels.clear()
        elif abs(position) > previous_abs + 1e-12:
            self.grid_layer_index = min(self.layers, self.grid_layer_index + 1)
        self.current_exposure = position
        if abs(position) >= previous_abs - 1e-12:
            self.latest_add_price = float(fill_price)
        if self.pending_target is not None and abs(position - self.pending_target) <= 1e-12:
            self.pending_target = None

    def initial_target(self, side: int) -> float:
        if side not in (-1, 1):
            raise ValueError("side must be -1 or 1")
        target = side * min(self._fraction_for_layer(0), self.max_abs_exposure)
        self.pending_target = target
        return target

    def add_target(
        self,
        *,
        price: float,
        atr: float,
        direction: PyramidDirection,
    ) -> float | None:
        if price <= 0 or atr <= 0:
            raise ValueError("price and ATR must be positive")
        if (
            self.episode_side == 0
            or self.latest_add_price is None
            or self.pending_target is not None
        ):
            return None
        if (
            self.grid_layer_index >= self.layers
            or abs(self.current_exposure) >= self.max_abs_exposure - 1e-12
        ):
            return None
        signed_move = self.episode_side * (price - self.latest_add_price)
        threshold = self.step_atr * atr
        qualifies = (
            signed_move >= threshold
            if direction is PyramidDirection.FAVORABLE
            else signed_move <= -threshold
        )
        if not qualifies:
            return None
        target_abs = min(
            abs(self.current_exposure) + self._fraction_for_layer(self.grid_layer_index),
            self.max_abs_exposure,
        )
        self.pending_target = self.episode_side * target_abs
        return self.pending_target

    def reduction_target(self, *, level_id: str, fraction: float = 0.5) -> float | None:
        if not level_id or not 0 <= fraction <= 1:
            raise ValueError("level_id is required and fraction must be in [0, 1]")
        if self.episode_side == 0 or self.pending_target is not None:
            return None
        if level_id in self.completed_reduction_levels:
            return None
        self.completed_reduction_levels.add(level_id)
        target = self.current_exposure * (1.0 - fraction)
        if abs(target) <= 1e-12:
            target = 0.0
        self.pending_target = target
        return target


class LevelEvent(str, Enum):
    UNTOUCHED = "untouched"
    BROKEN_ABOVE = "broken_above"
    BROKEN_BELOW = "broken_below"
    RETESTING_FROM_ABOVE = "retesting_from_above"
    RETESTING_FROM_BELOW = "retesting_from_below"
    RECLAIMED = "reclaimed"
    REJECTED = "rejected"


@dataclass
class LevelEventState:
    """Historical breakout/retest/rejection state for one explicit level."""

    state: LevelEvent = LevelEvent.UNTOUCHED

    def update(
        self,
        *,
        previous_close: float | None,
        open_: float,
        high: float,
        low: float,
        close: float,
        level: float,
        tolerance: float,
    ) -> LevelEvent:
        if low > high or tolerance < 0:
            raise ValueError("invalid bar range or tolerance")
        interacted = low <= level + tolerance and high >= level - tolerance
        if self.state is LevelEvent.UNTOUCHED and previous_close is not None:
            if previous_close <= level and close > level + tolerance:
                self.state = LevelEvent.BROKEN_ABOVE
            elif previous_close >= level and close < level - tolerance:
                self.state = LevelEvent.BROKEN_BELOW
            return self.state
        if self.state is LevelEvent.BROKEN_ABOVE and interacted:
            self.state = LevelEvent.RETESTING_FROM_ABOVE
        elif self.state is LevelEvent.BROKEN_BELOW and interacted:
            self.state = LevelEvent.RETESTING_FROM_BELOW
        if self.state is LevelEvent.RETESTING_FROM_ABOVE:
            if close > level and close >= open_:
                self.state = LevelEvent.RECLAIMED
            elif close < level - tolerance:
                self.state = LevelEvent.REJECTED
        elif self.state is LevelEvent.RETESTING_FROM_BELOW:
            if close < level and close <= open_:
                self.state = LevelEvent.RECLAIMED
            elif close > level + tolerance:
                self.state = LevelEvent.REJECTED
        return self.state


@dataclass(frozen=True)
class SessionFlattenModule:
    """
    Calendar control that proposes flat before UTC midnight.

    The module only decides *when* a flat target is due.  Orders and fills stay
    in the normal execution contract, and no synthetic 24:00 price is created.
    """

    module_id: str = "SESSION_FLATTEN_UTC_V1"
    execution_step_ns: int = 60_000_000_000

    def should_flatten(self, *, decision_time_ns: int, execution_lag_ns: int) -> bool:
        if execution_lag_ns < 0 or self.execution_step_ns <= 0:
            raise ValueError("invalid execution lag or step")
        return (decision_time_ns + execution_lag_ns + self.execution_step_ns) % DAY_NS == 0


@dataclass
class SessionRiskState:
    """UTC-session execution feedback for explicit daily loss/profit limits."""

    session_start_ns: int | None = None
    realized_pnl: float = 0.0
    unrealized_pnl: float = 0.0
    entry_count: int = 0

    def update(
        self,
        *,
        event_time_ns: int,
        realized_pnl_delta: float = 0.0,
        unrealized_pnl: float = 0.0,
        executed_entry: bool = False,
    ) -> None:
        session = event_time_ns // DAY_NS * DAY_NS
        if session != self.session_start_ns:
            self.session_start_ns = session
            self.realized_pnl = 0.0
            self.unrealized_pnl = 0.0
            self.entry_count = 0
        self.realized_pnl += float(realized_pnl_delta)
        self.unrealized_pnl = float(unrealized_pnl)
        self.entry_count += int(executed_entry)

    @property
    def total_pnl(self) -> float:
        return self.realized_pnl + self.unrealized_pnl

    def loss_limit_hit(self, limit: float) -> bool:
        if limit <= 0:
            raise ValueError("loss limit must be positive")
        return self.total_pnl <= -limit

    def entry_limit_hit(self, maximum_entries: int) -> bool:
        if maximum_entries <= 0:
            raise ValueError("maximum entries must be positive")
        return self.entry_count >= maximum_entries
