"""
Isolated state contract for the maker-only execution experiment.

The canonical strategies continue to emit target positions.  This module only
decides what passive order delta is required from the *filled* position and
how a resting remainder is treated at the next one-minute decision.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class MakerDataTier(StrEnum):
    L3_MBO = "TIER_1_L3_MBO_TRADES"
    L2_MBP = "TIER_2_L2_MBP_TRADES"
    L1 = "TIER_3_L1_QUOTES_TRADES"
    TRADE_ONLY = "TIER_4_TRADE_ONLY"
    UNSUPPORTED = "DATA_BLOCKED"


@dataclass(frozen=True)
class MakerExperimentConfig:
    execution_mode: str = "MAKER_ONLY"
    maker_policy: str = "NEXT_DECISION_CANCEL"
    post_only: bool = True
    trade_execution: bool = True
    queue_position: bool = False
    maker_fee_rate: float = 0.0
    fill_probability: float = 1.0
    random_seed: int = 7


class MakerLifecyclePolicy(StrEnum):
    NEXT_DECISION_CANCEL = "NEXT_DECISION_CANCEL"
    GTC_UNTIL_SIGNAL_INVALID = "GTC_UNTIL_SIGNAL_INVALID"
    PASSIVE_CANCEL_REQUOTE_15S = "PASSIVE_CANCEL_REQUOTE_15S"


@dataclass
class PureMakerLifecycleState:
    """Filled-position state shared by the frozen P1/P2 lifecycle policies."""

    actual_position: float = 0.0
    desired_target: float = 0.0
    resting_remaining: float = 0.0
    canceled_quantity: float = 0.0
    filled_quantity: float = 0.0

    def set_target(self, desired_target: float) -> float:
        self.desired_target = float(desired_target)
        return self.required_delta

    def cancel_remainder(self) -> float:
        canceled = abs(self.resting_remaining)
        self.canceled_quantity += canceled
        self.resting_remaining = 0.0
        return canceled

    def align_resting_quantity(self, signed_quantity: float) -> None:
        quantity = float(signed_quantity)
        required = self.required_delta
        if required and quantity * required < 0:
            raise ValueError("native resting quantity changed order direction")
        self.resting_remaining = quantity

    def apply_fill(self, signed_quantity: float) -> None:
        quantity = float(signed_quantity)
        if self.resting_remaining and quantity * self.resting_remaining < 0:
            raise ValueError("fill direction conflicts with resting order")
        if abs(quantity) > abs(self.resting_remaining) + 1e-12:
            raise ValueError("fill exceeds resting quantity")
        self.actual_position += quantity
        self.resting_remaining -= quantity
        self.filled_quantity += abs(quantity)

    @property
    def required_delta(self) -> float:
        return self.desired_target - self.actual_position

    @property
    def target_error(self) -> float:
        return abs(self.required_delta)


@dataclass
class NextDecisionCancelState:
    """Small deterministic policy state; fills, not targets, move position."""

    actual_position: float = 0.0
    desired_target: float = 0.0
    resting_remaining: float = 0.0
    canceled_quantity: float = 0.0
    filled_quantity: float = 0.0

    def next_decision(self, desired_target: float) -> float:
        """Cancel the old remainder and return the newly required signed delta."""
        self.canceled_quantity += abs(self.resting_remaining)
        self.resting_remaining = 0.0
        self.desired_target = float(desired_target)
        delta = self.desired_target - self.actual_position
        self.resting_remaining = delta
        return delta

    def apply_fill(self, signed_quantity: float) -> None:
        """Apply a native ``OrderFilled`` quantity to the virtual position."""
        quantity = float(signed_quantity)
        if self.resting_remaining and quantity * self.resting_remaining < 0:
            raise ValueError("fill direction conflicts with resting order")
        if abs(quantity) > abs(self.resting_remaining) + 1e-12:
            raise ValueError("fill exceeds resting quantity")
        self.actual_position += quantity
        self.resting_remaining -= quantity
        self.filled_quantity += abs(quantity)

    def align_resting_quantity(self, signed_quantity: float) -> None:
        """Align state to the quantity accepted at native instrument precision."""
        quantity = float(signed_quantity)
        if self.resting_remaining and quantity * self.resting_remaining < 0:
            raise ValueError("native resting quantity changed order direction")
        self.resting_remaining = quantity

    @property
    def target_error(self) -> float:
        return abs(self.desired_target - self.actual_position)


def queue_mode_allowed(tier: MakerDataTier) -> bool:
    """Queue tracking is enabled only with depth/order data, never trades alone."""
    return tier in {MakerDataTier.L2_MBP, MakerDataTier.L3_MBO}


def passive_trade_only_price(last_trade: float, tick_size: float, signed_delta: float) -> float:
    """Explicit weak-tier price rule; never label this as BBO-based maker realism."""
    if signed_delta == 0:
        raise ValueError("zero delta has no order side")
    return last_trade - tick_size if signed_delta > 0 else last_trade + tick_size
