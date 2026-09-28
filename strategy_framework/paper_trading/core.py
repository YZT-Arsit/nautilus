"""
Deterministic paper/shadow execution on production market data.

This module is intentionally small and auditable.  Signal generation remains
in the existing strategy/Nautilus path.  These classes consume frozen target
decisions and normalized market events, persist those events, and simulate two
execution views:

* ``FIRST_TICK_SHADOW``: the first trade at or after the decision timestamp.
* ``MAKER_PAPER``: Nautilus ``LIMIT`` + ``post_only=True`` at passive BBO with
  ``GTC_UNTIL_SIGNAL_INVALID`` lifecycle.

No method can send an order to an exchange.  ``LiveOrderSubmissionDisabled``
is a hard boundary which always raises.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
from collections.abc import Iterable
from dataclasses import asdict
from dataclasses import dataclass
from datetime import UTC
from datetime import datetime
from decimal import ROUND_DOWN
from decimal import ROUND_UP
from decimal import Decimal
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from data_engine.events import BarEvent
from data_engine.events import FundingRateEvent
from data_engine.events import QuoteEvent
from data_engine.events import TradeEvent
from strategy_framework.backends.nautilus_maker import NativeMakerHarness


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def deterministic_event_id(kind: str, *parts: Any) -> str:
    payload = _canonical_json([kind, *parts]).encode()
    return f"{kind.lower()}_{hashlib.sha256(payload).hexdigest()[:24]}"


class LiveOrderSubmissionDisabled(RuntimeError):
    """Hard safety boundary: production order submission is never available."""

    @staticmethod
    def submit_order(*_args: Any, **_kwargs: Any) -> None:
        raise LiveOrderSubmissionDisabled(
            "production order submission is disabled; paper orders must use the local Nautilus matcher"
        )


@dataclass(frozen=True)
class ExchangeFilter:
    tick_size: float
    step_size: float
    min_qty: float
    min_notional: float
    observed_at_ns: int
    source: str

    def round_price(self, price: float, side: str) -> float:
        unit = Decimal(str(self.tick_size))
        value = Decimal(str(price)) / unit
        mode = ROUND_DOWN if side == "BUY" else ROUND_UP
        return float(value.to_integral_value(rounding=mode) * unit)

    def round_quantity(self, quantity: float, price: float) -> float:
        unit = Decimal(str(self.step_size))
        value = (Decimal(str(abs(quantity))) / unit).to_integral_value(rounding=ROUND_DOWN) * unit
        rounded = float(value)
        if rounded + 1e-15 < self.min_qty or rounded * price + 1e-12 < self.min_notional:
            return 0.0
        return rounded


class CausalBarAggregator:
    """Build bars ``[t, t+T)`` and expose them only at ``t+T`` or later."""

    def __init__(self, instrument_id: str, interval_minutes: int) -> None:
        if interval_minutes not in {1, 10, 15}:
            raise ValueError("paper timeframes are frozen to 1m/10m/15m")
        self.instrument_id = instrument_id
        self.interval_ns = interval_minutes * 60 * 1_000_000_000
        self._start_ns: int | None = None
        self._prices: list[float] = []
        self._volume = 0.0
        self._count = 0

    def _completed(self) -> BarEvent:
        assert self._start_ns is not None
        assert self._prices
        return BarEvent(
            open=self._prices[0], high=max(self._prices), low=min(self._prices),
            close=self._prices[-1], volume=self._volume,
            trade_count=self._count, instrument_id=self.instrument_id,
            event_time_ns=self._start_ns + self.interval_ns,
        )

    def on_trade(self, event: TradeEvent) -> list[BarEvent]:
        if event.instrument_id != self.instrument_id:
            return []
        bucket = event.event_time_ns // self.interval_ns * self.interval_ns
        completed: list[BarEvent] = []
        if self._start_ns is None:
            self._start_ns = bucket
        elif bucket > self._start_ns:
            completed.append(self._completed())
            self._start_ns = bucket
            self._prices = []
            self._volume = 0.0
            self._count = 0
        elif bucket < self._start_ns:
            raise ValueError("out-of-order trade would violate causal bar construction")
        self._prices.append(float(event.price))
        self._volume += float(event.quantity)
        self._count += 1
        return completed

    def flush(self, watermark_ns: int) -> list[BarEvent]:
        if self._start_ns is None or not self._prices:
            return []
        if watermark_ns < self._start_ns + self.interval_ns:
            return []
        bar = self._completed()
        self._start_ns = None
        self._prices = []
        self._volume = 0.0
        self._count = 0
        return [bar]


class AppendOnlyMarketDataRecorder:
    """Append normalized production market data to replayable UTC partitions."""

    def __init__(self, root: Path, source: str) -> None:
        self.root = Path(root)
        self.source = source
        self._seen: set[str] = set()
        self._files: set[Path] = set()
        for path in self.root.rglob("events.jsonl") if self.root.exists() else ():
            self._files.add(path)
            with path.open(encoding="utf-8") as handle:
                for line in handle:
                    if line.strip():
                        self._seen.add(str(json.loads(line)["event_id"]))

    @staticmethod
    def _payload(event: Any) -> dict[str, Any]:
        fields = asdict(event)
        fields["event_class"] = type(event).__name__
        return fields

    def append(self, event: Any) -> bool:
        payload = self._payload(event)
        event_id = deterministic_event_id(
            payload["event_class"], payload.get("instrument_id"),
            payload.get("event_time_ns"), payload.get("trade_id"),
            payload.get("update_id"), payload.get("price"), payload.get("quantity"),
        )
        if event_id in self._seen:
            return False
        self._seen.add(event_id)
        ts = int(payload["event_time_ns"])
        date = datetime.fromtimestamp(ts / 1e9, tz=UTC).date().isoformat()
        symbol = str(payload.get("instrument_id", "UNKNOWN")).split(".", 1)[0]
        path = self.root / f"symbol={symbol}" / f"date={date}" / "events.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        row = {
            "event_id": event_id, "source": self.source,
            "ts_exchange": ts, "ts_receive": payload.get("receive_time_ns"),
            "payload": payload,
        }
        with path.open("a", encoding="utf-8") as handle:
            handle.write(_canonical_json(row) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        self._files.add(path)
        return True

    def manifest(self) -> list[dict[str, Any]]:
        rows = []
        for path in sorted(self._files):
            content = path.read_bytes()
            rows.append({
                "path": path.relative_to(self.root).as_posix(),
                "bytes": len(content), "sha256": hashlib.sha256(content).hexdigest(),
                "rows": content.count(b"\n"), "source": self.source,
            })
        return rows


class AtomicPaperStateStore:
    """Atomic JSON checkpoint store for restart and idempotency."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    def load(self) -> dict[str, Any]:
        if not self.path.exists():
            return {}
        return json.loads(self.path.read_text(encoding="utf-8"))

    def save(self, state: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(json.dumps(state, sort_keys=True, indent=2) + "\n", encoding="utf-8")
        os.replace(temporary, self.path)


class MarketDataGapMonitor:
    def __init__(self, stale_after_ns: int, max_clock_drift_ns: int) -> None:
        self.stale_after_ns = int(stale_after_ns)
        self.max_clock_drift_ns = int(max_clock_drift_ns)
        self.last_event_ns: dict[str, int] = {}
        self.last_update_id: dict[str, int] = {}
        self.gaps: list[dict[str, Any]] = []
        self.blocked = False

    def observe(self, event: Any) -> None:
        iid = str(event.instrument_id)
        ts = int(event.event_time_ns)
        previous = self.last_event_ns.get(iid)
        if previous is not None and ts < previous:
            self._block(iid, ts, "OUT_OF_ORDER_EVENT")
        receive = getattr(event, "receive_time_ns", None)
        if receive is not None and abs(int(receive) - ts) > self.max_clock_drift_ns:
            self._block(iid, ts, "CLOCK_DRIFT_EXCEEDED")
        update = getattr(event, "update_id", None)
        if update is not None:
            prior_update = self.last_update_id.get(iid)
            if prior_update is not None and int(update) <= prior_update:
                self._block(iid, ts, "NON_MONOTONIC_SEQUENCE")
            self.last_update_id[iid] = int(update)
        self.last_event_ns[iid] = max(ts, previous or ts)

    def assert_fresh(self, instrument_id: str, now_ns: int) -> None:
        last = self.last_event_ns.get(instrument_id)
        if last is None or now_ns - last > self.stale_after_ns:
            self._block(instrument_id, now_ns, "STALE_OR_MISSING_MARKET_DATA")
        if self.blocked:
            raise RuntimeError("market-data gap kill switch is active")

    def _block(self, instrument_id: str, ts: int, reason: str) -> None:
        self.blocked = True
        self.gaps.append({"instrument_id": instrument_id, "event_time_ns": ts, "reason": reason})


@dataclass
class PaperFill:
    fill_id: str
    event_time_ns: int
    side: str
    quantity: float
    price: float
    fee: float


class PaperAccount:
    """Single-candidate isolated account with fill-based position accounting."""

    def __init__(self, initial_capital: float, target_notional: float, fee_rate: float) -> None:
        self.initial_capital = float(initial_capital)
        self.target_notional = float(target_notional)
        self.fee_rate = float(fee_rate)
        self.cash = float(initial_capital)
        self.position_qty = 0.0
        self.avg_price = 0.0
        self.realized_pnl = 0.0
        self.funding_pnl = 0.0
        self.fees = 0.0
        self.total_turnover_raw = 0.0
        self.daily_turnover: dict[str, float] = {}
        self._booked_ids: set[str] = set()

    def apply_fill(self, fill: PaperFill) -> bool:
        if fill.fill_id in self._booked_ids:
            return False
        self._booked_ids.add(fill.fill_id)
        signed = fill.quantity if fill.side == "BUY" else -fill.quantity
        old = self.position_qty
        new = old + signed
        fee = abs(fill.quantity * fill.price) * self.fee_rate if fill.fee == 0 else fill.fee
        self.fees += fee
        self.cash -= signed * fill.price + fee
        if old == 0 or old * signed > 0:
            combined = abs(old) + abs(signed)
            self.avg_price = (abs(old) * self.avg_price + abs(signed) * fill.price) / combined
        else:
            closed = min(abs(old), abs(signed))
            self.realized_pnl += closed * (fill.price - self.avg_price) * (1 if old > 0 else -1)
            if new == 0:
                self.avg_price = 0.0
            elif old * new < 0:
                self.avg_price = fill.price
        self.position_qty = new
        increment = abs(signed * fill.price) / self.target_notional
        self.total_turnover_raw += increment
        day = datetime.fromtimestamp(fill.event_time_ns / 1e9, tz=UTC).date().isoformat()
        self.daily_turnover[day] = self.daily_turnover.get(day, 0.0) + increment
        return True

    def apply_funding(self, event: FundingRateEvent, mark_price: float) -> float:
        event_id = deterministic_event_id("funding", event.instrument_id, event.event_time_ns)
        if event_id in self._booked_ids:
            return 0.0
        self._booked_ids.add(event_id)
        mark = float(event.mark_price if event.mark_price is not None else mark_price)
        payment = -self.position_qty * mark * float(event.funding_rate)
        self.cash += payment
        self.funding_pnl += payment
        return payment

    def equity(self, mark_price: float) -> float:
        return self.cash + self.position_qty * float(mark_price)

    def snapshot(self) -> dict[str, Any]:
        return {
            "cash": self.cash, "position_qty": self.position_qty,
            "avg_price": self.avg_price, "realized_pnl": self.realized_pnl,
            "funding_pnl": self.funding_pnl, "fees": self.fees,
            "total_turnover_raw": self.total_turnover_raw,
            "daily_turnover": dict(self.daily_turnover),
            "booked_ids": sorted(self._booked_ids),
        }

    def restore(self, state: dict[str, Any]) -> None:
        for field in ("cash", "position_qty", "avg_price", "realized_pnl", "funding_pnl", "fees", "total_turnover_raw"):
            setattr(self, field, float(state[field]))
        self.daily_turnover = {str(k): float(v) for k, v in state["daily_turnover"].items()}
        self._booked_ids = set(state["booked_ids"])


class FirstTickShadowExecutor:
    def __init__(self, account: PaperAccount, instrument_id: str) -> None:
        self.account = account
        self.instrument_id = instrument_id
        self.target = 0.0
        self.pending: tuple[int, float, str] | None = None
        self.fills: list[PaperFill] = []

    def on_target(self, decision_time_ns: int, target_position: float) -> None:
        signal_id = deterministic_event_id("signal", self.instrument_id, decision_time_ns, target_position)
        self.target = float(target_position)
        self.pending = (int(decision_time_ns), self.target, signal_id)

    def on_trade(self, trade: TradeEvent) -> PaperFill | None:
        if self.pending is None or trade.event_time_ns < self.pending[0]:
            return None
        decision_ns, target, signal_id = self.pending
        desired_qty = target * self.account.target_notional / float(trade.price)
        delta = desired_qty - self.account.position_qty
        self.pending = None
        if abs(delta) <= 1e-15:
            return None
        side = "BUY" if delta > 0 else "SELL"
        fill = PaperFill(
            fill_id=deterministic_event_id("fill", "FIRST_TICK_SHADOW", signal_id, trade.trade_id),
            event_time_ns=int(trade.event_time_ns), side=side,
            quantity=abs(delta), price=float(trade.price), fee=0.0,
        )
        self.account.apply_fill(fill)
        self.fills.append(fill)
        return fill


class MakerPaperExecutor:
    """Local Nautilus L1 post-only matcher with frozen GTC-until-invalid policy."""

    def __init__(
        self, account: PaperAccount, instrument_filter: ExchangeFilter,
        harness: NativeMakerHarness | None = None,
    ) -> None:
        self.account = account
        self.filter = instrument_filter
        self.harness = harness or NativeMakerHarness(liquidity_consumption=True, queue_position=False)
        self.instrument_id = str(self.harness.instrument.id)
        self.latest_quote: QuoteEvent | None = None
        self.target = 0.0
        self.order: Any | None = None
        self._fill_cursor = 0
        self.orders_submitted = 0
        self.cancels = 0
        self.fills: list[PaperFill] = []

    def on_quote(self, quote: QuoteEvent) -> None:
        self.latest_quote = quote
        self.harness.quote(
            bid=quote.bid_price, ask=quote.ask_price,
            bid_size=quote.bid_size or 0.0, ask_size=quote.ask_size or 0.0,
            ts_event=quote.event_time_ns, ts_init=quote.receive_time_ns or quote.event_time_ns,
        )
        self._sync_fills()

    def on_trade(self, trade: TradeEvent) -> None:
        aggressor = "SELLER" if trade.is_buyer_maker else "BUYER"
        self.harness.trade(
            price=trade.price, size=trade.quantity, aggressor=aggressor,
            ts=trade.event_time_ns, trade_id=str(trade.trade_id or trade.event_time_ns),
        )
        self._sync_fills()

    def on_target(self, decision_time_ns: int, target_position: float) -> None:
        changed = not math.isclose(float(target_position), self.target, abs_tol=1e-12)
        self.target = float(target_position)
        if changed and self.order is not None and self.order.is_open:
            self.harness.cancel(self.order)
            self.cancels += 1
        if self.latest_quote is None:
            return
        desired_qty = self.target * self.account.target_notional / self.latest_quote.mid_price
        delta = desired_qty - self.account.position_qty
        if abs(delta) <= 1e-15:
            return
        if self.order is not None and self.order.is_open:
            return
        side = "BUY" if delta > 0 else "SELL"
        passive = self.latest_quote.bid_price if side == "BUY" else self.latest_quote.ask_price
        price = self.filter.round_price(passive, side)
        quantity = self.filter.round_quantity(abs(delta), price)
        if quantity == 0:
            return
        order_id = deterministic_event_id("order", self.instrument_id, decision_time_ns, side, price, quantity)
        self.order = self.harness.limit(
            side=side, price=price, quantity=quantity, post_only=True, client_order_id=order_id,
        )
        self.orders_submitted += 1
        self._sync_fills()

    def _sync_fills(self) -> None:
        from nautilus_trader.model.events import OrderFilled

        native = self.harness.events(OrderFilled)
        for event in native[self._fill_cursor :]:
            side = str(event.order_side.name)
            quantity = float(str(event.last_qty))
            price = float(str(event.last_px))
            fill = PaperFill(
                fill_id=deterministic_event_id("fill", str(event.trade_id), str(event.client_order_id)),
                event_time_ns=int(event.ts_event), side=side,
                quantity=quantity, price=price, fee=0.0,
            )
            if self.account.apply_fill(fill):
                self.fills.append(fill)
        self._fill_cursor = len(native)


def daily_turnover_summary(
    daily: dict[str, float] | pd.Series,
    *, complete_start: str, complete_end_exclusive: str,
) -> dict[str, float | int]:
    """Summarize complete UTC days including zero-turnover days."""
    index = pd.date_range(complete_start, pd.Timestamp(complete_end_exclusive) - pd.Timedelta(days=1), freq="D")
    if isinstance(daily, pd.Series):
        values = {str(pd.Timestamp(k).date()): float(v) for k, v in daily.items()}
    else:
        values = {str(k): float(v) for k, v in daily.items()}
    array = np.array([values.get(day.date().isoformat(), 0.0) for day in index], dtype=float)
    active = array[array > 0]
    return {
        "complete_turnover_days": len(array),
        "mean_daily_turnover_raw": float(array.mean()) if len(array) else float("nan"),
        "mean_daily_turnover_pct": float(array.mean() * 100) if len(array) else float("nan"),
        "median_daily_turnover_raw": float(np.median(array)) if len(array) else float("nan"),
        "median_daily_turnover_pct": float(np.median(array) * 100) if len(array) else float("nan"),
        "P90_daily_turnover_pct": float(np.quantile(array, 0.90) * 100) if len(array) else float("nan"),
        "P95_daily_turnover_pct": float(np.quantile(array, 0.95) * 100) if len(array) else float("nan"),
        "max_daily_turnover_pct": float(array.max() * 100) if len(array) else float("nan"),
        "active_day_mean_turnover_pct": float(active.mean() * 100) if len(active) else 0.0,
        "total_turnover_raw": float(array.sum()),
        "total_turnover_pct": float(array.sum() * 100),
    }


def replay_digest(records: Iterable[dict[str, Any]]) -> str:
    return hashlib.sha256(_canonical_json(list(records)).encode()).hexdigest()
