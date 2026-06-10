"""Dependency-free intent fill simulator (fallback / reference path).

This is **not** the Nautilus ``BacktestEngine``. It is a small, testable
reference that turns :class:`OrderIntent` / :class:`PositionIntent` into
:class:`FillRecord` s and tracks positions / realized & unrealized PnL — without
any Nautilus Trader dependency. The Nautilus backend uses it for
``mode="simulated"`` so the ordinary path runs and reports PnL with no native
components installed.

Accounting is intentionally simple: average-price positions, signed quantity
(long positive / short negative), realized PnL booked on the closing portion of a
trade. No commission/slippage unless explicitly configured.
"""
from __future__ import annotations

from typing import Any

from strategy_framework.execution.intents import OrderIntent, PositionIntent
from strategy_framework.execution.reports import ExecutionReport, FillRecord, PositionRecord


class _Position:
    __slots__ = ("qty", "avg_price", "realized", "last_price")

    def __init__(self) -> None:
        self.qty = 0.0
        self.avg_price = 0.0
        self.realized = 0.0
        self.last_price = 0.0


class IntentFillSimulator:
    """Simulate fills/positions/PnL from a stream of intents."""

    def __init__(
        self,
        *,
        default_price_field: str = "close",
        allow_short: bool = False,
        backend: str = "nautilus_backtest",
    ) -> None:
        self._price_field = default_price_field
        self._allow_short = bool(allow_short)
        self._backend = backend
        self._fills: list[FillRecord] = []
        self._positions: dict[str, _Position] = {}
        self._intent_count = 0

    # -- helpers -------------------------------------------------------------

    def _pos(self, instrument_id: str) -> _Position:
        return self._positions.setdefault(instrument_id, _Position())

    def _resolve_price(self, intent: Any, event: Any) -> float:
        price = getattr(event, self._price_field, None)
        if price is None:
            price = (getattr(intent, "metadata", {}) or {}).get("price")
        if price is None:
            raise ValueError(
                f"cannot simulate fill for {intent.instrument_id!r}: no "
                f"{self._price_field!r} on event and no 'price' in intent metadata"
            )
        return float(price)

    def _apply(self, instrument_id: str, signed_delta: float, price: float) -> float:
        """Apply a signed trade; return the actually-filled (absolute) quantity."""
        pos = self._pos(instrument_id)
        c, a = pos.qty, pos.avg_price

        # allow_short=False: never let the position go negative (clamp a SELL to
        # at most the current long quantity; if flat, nothing fills).
        if not self._allow_short and c + signed_delta < 0:
            signed_delta = -c
        if signed_delta == 0:
            return 0.0

        if c == 0 or (c > 0) == (signed_delta > 0):
            # opening or increasing in the same direction -> weighted avg price
            new_qty = c + signed_delta
            pos.avg_price = (a * abs(c) + price * abs(signed_delta)) / abs(new_qty)
            pos.qty = new_qty
        else:
            # reducing / closing (and possibly flipping)
            closing = min(abs(signed_delta), abs(c))
            sign_c = 1.0 if c > 0 else -1.0
            pos.realized += closing * (price - a) * sign_c
            new_qty = c + signed_delta
            pos.qty = new_qty
            if abs(signed_delta) > abs(c):  # flipped past flat -> new position at price
                pos.avg_price = price
            elif new_qty == 0:
                pos.avg_price = 0.0
            # else: partial close, avg_price unchanged
        pos.last_price = price
        return abs(signed_delta)

    # -- public API ----------------------------------------------------------

    def on_intent(self, intent: Any, event: Any) -> FillRecord | None:
        """Process one intent; return a :class:`FillRecord` or ``None``."""
        if intent is None:
            return None
        self._intent_count += 1

        if isinstance(intent, PositionIntent):
            if intent.target != "FLAT":
                return None  # only FLAT is produced/handled today
            pos = self._pos(intent.instrument_id)
            if pos.qty == 0:
                return None
            side = "SELL" if pos.qty > 0 else "BUY"
            qty = abs(pos.qty)
        elif isinstance(intent, OrderIntent):
            side = intent.side
            qty = float(intent.quantity)
        else:
            return None

        if qty <= 0:
            return None

        price = self._resolve_price(intent, event)
        signed_delta = qty if side == "BUY" else -qty
        filled = self._apply(intent.instrument_id, signed_delta, price)
        if filled <= 0:
            return None

        fill = FillRecord(
            instrument_id=intent.instrument_id,
            side=side,
            quantity=filled,
            price=price,
            event_time_ns=getattr(intent, "event_time_ns", 0),
            source="simulated",
            metadata={"reason": getattr(intent, "reason", "")},
        )
        self._fills.append(fill)
        return fill

    def report(self) -> ExecutionReport:
        positions: list[PositionRecord] = []
        realized_total = 0.0
        unrealized_total = 0.0
        for instrument_id, pos in sorted(self._positions.items()):
            realized_total += pos.realized
            unrealized = pos.qty * (pos.last_price - pos.avg_price)
            unrealized_total += unrealized
            if pos.qty != 0:
                positions.append(
                    PositionRecord(
                        instrument_id=instrument_id,
                        quantity=pos.qty,
                        avg_price=pos.avg_price,
                        market_price=pos.last_price,
                        unrealized_pnl=unrealized,
                        realized_pnl=pos.realized,
                    )
                )
        return ExecutionReport(
            backend=self._backend,
            total_intents=self._intent_count,
            total_fills=len(self._fills),
            fills=list(self._fills),
            positions=positions,
            realized_pnl=realized_total,
            unrealized_pnl=unrealized_total,
            metadata={"mode": "simulated", "allow_short": self._allow_short},
        )
