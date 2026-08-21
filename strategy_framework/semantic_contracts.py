"""Versioned semantic contracts for qualitative strategy specifications.

The contracts in this module are project-wide policy.  They contain no
workbook parsing and own no execution state.  A compiled strategy records the
contract IDs it used so a result can be reproduced without the source workbook.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Mapping, Sequence


class SemanticProvenance(str, Enum):
    SOURCE_EXACT = "SOURCE_EXACT"
    STANDARD_CONTRACT_RESOLVED = "STANDARD_CONTRACT_RESOLVED"
    PARAMETER_DEFAULTED = "PARAMETER_DEFAULTED"


@dataclass(frozen=True)
class SemanticContract:
    contract_id: str
    version: int
    machine_definition: str
    parameters: tuple[str, ...] = ()
    default_parameters: tuple[tuple[str, float | int], ...] = ()
    provenance: SemanticProvenance = SemanticProvenance.STANDARD_CONTRACT_RESOLVED

    @property
    def versioned_id(self) -> str:
        return f"{self.contract_id}_V{self.version}"

    def defaults(self) -> dict[str, float | int]:
        return dict(self.default_parameters)


CONTRACTS: tuple[SemanticContract, ...] = (
    SemanticContract("STANDARD_RULESET_ALREADY_RESOLVABLE", 1,
                     "source rule has a unique standard computational interpretation",
                     provenance=SemanticProvenance.SOURCE_EXACT),
    SemanticContract("CONFLUENCE_AND", 1, "all explicitly required predicates are true"),
    SemanticContract("MTF_LATEST_COMPLETED_ALL_TRUE", 1,
                     "latest fully completed state from every required timeframe is true"),
    SemanticContract("TURN_SLOPE_SIGN_CHANGE", 1,
                     "previous slope is non-positive/non-negative and current slope changes sign"),
    SemanticContract("ATR14_DEFAULT", 1, "Wilder ATR with the centralized omitted-period default",
                     ("period",), (("period", 14),), SemanticProvenance.PARAMETER_DEFAULTED),
    SemanticContract("PERSISTENCE_2BAR", 1, "predicate is true for consecutive completed bars",
                     ("bars",), (("bars", 2),), SemanticProvenance.PARAMETER_DEFAULTED),
    SemanticContract("STABLE_CLOSE_2BAR", 1, "completed close remains beyond level",
                     ("bars",), (("bars", 2),), SemanticProvenance.PARAMETER_DEFAULTED),
    SemanticContract("CONFIRM_CLOSE_2BAR", 1, "two completed closes remain beyond boundary",
                     ("bars",), (("bars", 2),), SemanticProvenance.PARAMETER_DEFAULTED),
    SemanticContract("RECENT_EXTREME_PRIOR_20", 1,
                     "current value exceeds prior completed-window extreme; current excluded",
                     ("lookback",), (("lookback", 20),), SemanticProvenance.PARAMETER_DEFAULTED),
    SemanticContract("BOUNDED_INDICATOR_EXTREMES", 1,
                     "canonical indicator-specific high and low thresholds"),
    SemanticContract("EXPLICIT_LEVEL_SUPPORT_RESISTANCE", 1,
                     "explicit source level is used directly as support or resistance"),
    SemanticContract("LEVEL_TOLERANCE_ATR025", 1, "absolute level tolerance is ATR multiple",
                     ("atr_period", "atr_multiple"), (("atr_period", 14), ("atr_multiple", 0.25)),
                     SemanticProvenance.PARAMETER_DEFAULTED),
    SemanticContract("PULLBACK_AFTER_BREAKOUT", 1,
                     "prior breakout, later tolerance-zone interaction, then close back beyond level"),
    SemanticContract("REJECTION_AT_LEVEL", 1,
                     "tolerance-zone interaction followed by a close on the rejecting side"),
    SemanticContract("STABILIZE_MINIMAL_TRANSITION", 1,
                     "prior decline/rise stops on a completed close without requiring reversal"),
    SemanticContract("CONFIRMED_FRACTAL_2X2", 1,
                     "two left bars, pivot, two right bars; visible only after confirmation",
                     ("side_bars",), (("side_bars", 2),), SemanticProvenance.PARAMETER_DEFAULTED),
    SemanticContract("REGULAR_DIVERGENCE_CONFIRMED_PIVOTS", 1,
                     "regular divergence over two confirmed corresponding pivots"),
    SemanticContract("DIVERGENCE_LOOKBACK_60", 1, "confirmed divergence pivots are within lookback",
                     ("lookback",), (("lookback", 60),), SemanticProvenance.PARAMETER_DEFAULTED),
    SemanticContract("MEAN_REVERSION_TO_SOURCE_CENTER", 1,
                     "deviating value returns to or crosses its source-defined center"),
    SemanticContract("GRID_4L_ATR1_EQUAL", 1,
                     "four fill-anchored ATR-spaced layers with equal exposure and 1x cap",
                     ("layers", "atr_period", "atr_step", "max_abs_exposure", "layer_fraction"),
                     (("layers", 4), ("atr_period", 14), ("atr_step", 1.0),
                      ("max_abs_exposure", 1.0), ("layer_fraction", 0.25)),
                     SemanticProvenance.PARAMETER_DEFAULTED),
    SemanticContract("REDUCE_HALF_CURRENT", 1, "reduce half of current absolute exposure",
                     ("fraction",), (("fraction", 0.5),), SemanticProvenance.PARAMETER_DEFAULTED),
    SemanticContract("LAYERED_REDUCTION_EQUAL", 1,
                     "divide episode exposure equally across explicit targets or two default stages",
                     ("default_stages",), (("default_stages", 2),),
                     SemanticProvenance.PARAMETER_DEFAULTED),
    SemanticContract("ADD_QUARTER_EXPOSURE", 1, "add 0.25x exposure up to the 1x cap",
                     ("add_fraction", "max_abs_exposure"),
                     (("add_fraction", 0.25), ("max_abs_exposure", 1.0)),
                     SemanticProvenance.PARAMETER_DEFAULTED),
    SemanticContract("CHANNEL_LAST_BREAKOUT_STATE", 1,
                     "channel direction is the most recent completed upper/lower breakout state"),
    SemanticContract("GRID_SOURCE_LAYERS_EQUAL_EXPOSURE", 1,
                     "source-explicit finite layer count divides the 1x cap equally",
                     provenance=SemanticProvenance.PARAMETER_DEFAULTED),
    SemanticContract("PYRAMID_FAVORABLE_DIRECTION", 1,
                     "source pyramid wording adds only after a favorable ATR move"),
    SemanticContract("TOUCH_AS_THRESHOLD_CROSS", 1,
                     "touch is the first completed-observation crossing of the explicit threshold"),
)

REGISTRY: Mapping[str, SemanticContract] = {contract.versioned_id: contract for contract in CONTRACTS}

INDICATOR_EXTREMES: Mapping[str, tuple[float, float]] = {
    "RSI": (70.0, 30.0),
    "STOCHASTIC": (80.0, 20.0),
    "WILLIAMS_R": (-20.0, -80.0),
    "CCI": (100.0, -100.0),
}


def contract(contract_id: str) -> SemanticContract:
    try:
        return REGISTRY[contract_id]
    except KeyError:
        raise KeyError(f"unknown semantic contract: {contract_id}") from None


def all_required(values: Sequence[bool]) -> bool:
    return bool(values) and all(values)


def turn_up(older: float, previous: float, current: float) -> bool:
    return previous - older <= 0 and current - previous > 0


def turn_down(older: float, previous: float, current: float) -> bool:
    return previous - older >= 0 and current - previous < 0


def persistent(values: Sequence[bool], bars: int = 2) -> bool:
    if bars <= 0:
        raise ValueError("bars must be positive")
    return len(values) >= bars and all(values[-bars:])


def stable_above(closes: Sequence[float], levels: Sequence[float], bars: int = 2) -> bool:
    if len(closes) != len(levels):
        raise ValueError("closes and levels must have equal length")
    return persistent([close > level for close, level in zip(closes, levels)], bars)


def stable_below(closes: Sequence[float], levels: Sequence[float], bars: int = 2) -> bool:
    if len(closes) != len(levels):
        raise ValueError("closes and levels must have equal length")
    return persistent([close < level for close, level in zip(closes, levels)], bars)


def prior_new_high(current: float, prior: Sequence[float]) -> bool:
    return bool(prior) and current > max(prior)


def prior_new_low(current: float, prior: Sequence[float]) -> bool:
    return bool(prior) and current < min(prior)


def level_tolerance(atr: float, multiple: float = 0.25) -> float:
    if atr <= 0 or multiple < 0:
        raise ValueError("ATR must be positive and multiple non-negative")
    return atr * multiple


def near_level(price: float, level: float, tolerance: float) -> bool:
    if tolerance < 0:
        raise ValueError("tolerance cannot be negative")
    return abs(price - level) <= tolerance


def candle_interacts(low: float, high: float, level: float, tolerance: float) -> bool:
    if low > high or tolerance < 0:
        raise ValueError("invalid candle range or tolerance")
    return low <= level + tolerance and high >= level - tolerance


@dataclass
class PullbackState:
    direction: int
    breakout_seen: bool = False

    def __post_init__(self) -> None:
        if self.direction not in (-1, 1):
            raise ValueError("direction must be -1 or 1")

    def update(self, *, close: float, low: float, high: float, level: float, tolerance: float) -> bool:
        beyond = close > level if self.direction > 0 else close < level
        if beyond and not candle_interacts(low, high, level, tolerance):
            self.breakout_seen = True
            return False
        interacted = candle_interacts(low, high, level, tolerance)
        confirmed = self.breakout_seen and interacted and beyond
        if confirmed:
            self.breakout_seen = False
        return confirmed


def rejection(*, direction: int, open_: float, close: float, low: float, high: float,
              level: float, tolerance: float, require_candle_direction: bool = True) -> bool:
    if direction not in (-1, 1):
        raise ValueError("direction must be -1 or 1")
    if not candle_interacts(low, high, level, tolerance):
        return False
    side = close > level if direction > 0 else close < level
    candle = close > open_ if direction > 0 else close < open_
    return side and (candle or not require_candle_direction)


def stabilized(previous_delta: float, current_delta: float, *, after_decline: bool) -> bool:
    return (previous_delta < 0 and current_delta >= 0) if after_decline else (
        previous_delta > 0 and current_delta <= 0
    )


def grid_target(layer: int, *, direction: int, layers: int = 4,
                layer_fraction: float = 0.25, max_abs_exposure: float = 1.0) -> float:
    if direction not in (-1, 1) or not 0 <= layer <= layers:
        raise ValueError("invalid direction or layer")
    return direction * min(layer * layer_fraction, max_abs_exposure)


def reduce_current(exposure: float, fraction: float = 0.5) -> float:
    if not 0 <= fraction <= 1:
        raise ValueError("fraction must be in [0, 1]")
    return exposure * (1.0 - fraction)
