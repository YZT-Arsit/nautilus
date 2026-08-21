"""Composable, source-independent strategy risk/exit module contracts."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Protocol, runtime_checkable


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

    def __post_init__(self) -> None:
        if self.side not in (-1, 1):
            raise ValueError("side must be -1 or 1")
        if self.entry_price <= 0 or self.current_price <= 0 or self.atr <= 0:
            raise ValueError("prices and ATR must be positive")
        if not 0 < self.initial_exposure <= 1:
            raise ValueError("initial_exposure must be in (0, 1]")
        if self.adx is not None and self.adx < 0:
            raise ValueError("adx cannot be negative")


@dataclass(frozen=True)
class ModuleDecision:
    target_exposure: float
    reason: str


@runtime_checkable
class StrategyModule(Protocol):
    module_id: str

    def evaluate(self, context: ModuleContext) -> ModuleDecision:
        ...


@dataclass(frozen=True)
class AtrLadderExitModule:
    """Deterministic ATR profit ladder plus hard stop.

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
            fraction for level, fraction in zip(self.profit_levels_atr, self.reduction_fractions)
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
        hit = (
            context.side > 0 and context.current_price < context.lower_channel
        ) or (
            context.side < 0 and context.current_price > context.upper_channel
        )
        return ModuleDecision(0.0, "donchian_exit" ) if hit else ModuleDecision(context.initial_exposure, "hold")


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
        multiplier = 1.0 if context.adx > self.full_threshold else (
            self.medium_exposure if context.adx >= self.medium_threshold else self.low_exposure
        )
        return ModuleDecision(context.initial_exposure * multiplier, "adx_exposure_regime")


class PyramidDirection(str, Enum):
    FAVORABLE = "favorable"
    ADVERSE = "adverse"


@dataclass
class GridPyramidState:
    """Fill-reconciled decision state for a finite grid/pyramid episode.

    It proposes exposure targets but does not execute orders. A proposal stays
    pending until a confirmed fill synchronizes the position, so repeated
    observations cannot append the same layer more than once.
    """

    layers: int = 4
    step_atr: float = 1.0
    max_abs_exposure: float = 1.0
    layer_fraction: float = 0.25
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
        target = side * min(self.layer_fraction, self.max_abs_exposure)
        self.pending_target = target
        return target

    def add_target(
        self, *, price: float, atr: float, direction: PyramidDirection,
    ) -> float | None:
        if price <= 0 or atr <= 0:
            raise ValueError("price and ATR must be positive")
        if self.episode_side == 0 or self.latest_add_price is None or self.pending_target is not None:
            return None
        if self.grid_layer_index >= self.layers or abs(self.current_exposure) >= self.max_abs_exposure - 1e-12:
            return None
        signed_move = self.episode_side * (price - self.latest_add_price)
        threshold = self.step_atr * atr
        qualifies = signed_move >= threshold if direction is PyramidDirection.FAVORABLE else signed_move <= -threshold
        if not qualifies:
            return None
        target_abs = min(abs(self.current_exposure) + self.layer_fraction, self.max_abs_exposure)
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
        self, *, previous_close: float | None, open_: float, high: float,
        low: float, close: float, level: float, tolerance: float,
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
    """Calendar control that proposes flat before UTC midnight.

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
        self, *, event_time_ns: int, realized_pnl_delta: float = 0.0,
        unrealized_pnl: float = 0.0, executed_entry: bool = False,
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
