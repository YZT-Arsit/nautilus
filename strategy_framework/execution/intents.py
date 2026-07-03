"""Dependency-free execution intent model.

An *intent* is what the strategy *wants* to happen, expressed without any
execution-engine coupling. Backends translate intents into concrete orders.

This module must stay dependency-free — **no Nautilus Trader imports**.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


@dataclass(frozen=True)
class OrderIntent:
    """An intended order, decoupled from any broker/engine."""

    instrument_id: str
    side: Literal["BUY", "SELL"]
    quantity: float
    event_time_ns: int
    reason: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class PositionIntent:
    """An intended target position (used when SELL means 'go flat')."""

    instrument_id: str
    target: Literal["LONG", "SHORT", "FLAT"]
    quantity: float
    event_time_ns: int
    reason: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Rich per-bar order plan (opt-in; enables sized/pyramiding strategies)
# ---------------------------------------------------------------------------
#
# Simple strategies return a ``"BUY"/"SELL"/"HOLD"`` string and let
# :class:`SignalToOrderPolicy` size a single fixed-quantity order. That single
# net-one-unit-per-bar model cannot express strategies that size by their own
# risk model or pyramid multiple units in one bar (e.g. the Turtle system).
#
# For those, a strategy may instead return a :class:`PlannedSignal` from
# ``on_snapshot``. It IS a plain ``str`` (a short display label such as
# ``"BUY"``/``"EXIT"``/``"HOLD"``) so every existing consumer — the event table,
# ``SignalRecorder`` counts, the string signal path — keeps working unchanged;
# backends that understand the rich path additionally read ``.actions`` and
# execute the sized :class:`TradeAction` list directly (bypassing the fixed-qty
# policy, since sizing has already been decided by the strategy).

@dataclass(frozen=True)
class TradeAction:
    """One sized order a strategy wants executed this bar (rich-signal path).

    ``close_all=True`` flattens the entire current position (quantity/side are
    then ignored — it maps to a ``PositionIntent(target="FLAT")``). Otherwise a
    ``side``/``quantity`` order is submitted. ``fill_price`` (when set) is the
    price the strategy wants the order filled at — the simulated fill model
    prefers it over the bar's ``price_field``, so channel-breakout / N-stop /
    gap-to-open fills replay faithfully.
    """

    side: Literal["BUY", "SELL"] = "BUY"
    quantity: float = 0.0
    reason: str = ""
    close_all: bool = False
    fill_price: float | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


class PlannedSignal(str):
    """A display-label string that also carries a sized order plan.

    Subclasses ``str`` so it hashes/compares/prints exactly as its label (kept
    backward-compatible with the string signal channel). Rich-aware backends read
    ``.actions`` — a possibly empty tuple of :class:`TradeAction`. An empty tuple
    means "no orders this bar" (a ``HOLD``).
    """

    __slots__ = ("actions",)

    def __new__(cls, label: str, actions: "tuple[TradeAction, ...] | list[TradeAction]" = ()):  # noqa: UP037
        obj = super().__new__(cls, label)
        obj.actions = tuple(actions)
        return obj
