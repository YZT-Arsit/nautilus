"""Active-active canonical public-market-data merge and durable WAL primitives."""

from __future__ import annotations

import hashlib
import json
import os
import time
from collections import deque
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from data_engine.events import FundingRateEvent, QuoteEvent, TradeEvent


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def event_identity(event: Any) -> tuple:
    instrument = str(event.instrument_id)
    if isinstance(event, TradeEvent):
        if event.trade_id is not None:
            return ("trade", instrument, str(event.trade_id))
        return (
            "trade", instrument, int(event.event_time_ns), float(event.price),
            float(event.quantity), event.side,
        )
    if isinstance(event, QuoteEvent):
        if event.update_id is not None:
            return ("quote", instrument, int(event.update_id))
        return (
            "quote", instrument, int(event.event_time_ns), float(event.bid_price),
            event.bid_size, float(event.ask_price), event.ask_size,
        )
    if isinstance(event, FundingRateEvent):
        return ("funding_rate", instrument, int(event.event_time_ns), float(event.funding_rate))
    raise TypeError(f"unsupported canonical event: {type(event).__name__}")


def event_sort_key(event: Any) -> tuple:
    rank = {"trade": 0, "quote": 1, "funding_rate": 2}[str(event.event_type)]
    identity = event_identity(event)
    return int(event.event_time_ns), rank, canonical_json(identity)


@dataclass
class PendingCanonicalEvent:
    event: Any
    identity: tuple
    first_arrival_mono: float
    routes: set[str] = field(default_factory=set)
    receive_times_ns: dict[str, int] = field(default_factory=dict)


class CanonicalMarketDataMerger:
    """Deduplicate redundant routes and emit deterministic exchange-time order."""

    def __init__(
        self, *, reorder_delay_ms: float = 250.0, clock=time.monotonic,
        dedup_capacity: int = 2_000_000,
    ) -> None:
        self.reorder_delay_seconds = float(reorder_delay_ms) / 1_000.0
        self.clock = clock
        self.pending: dict[tuple, PendingCanonicalEvent] = {}
        self.emitted: set[tuple] = set()
        self.emitted_order: deque[tuple] = deque()
        self.dedup_capacity = int(dedup_capacity)
        # Binance trade, BBO, and funding streams do not expose one shared
        # total-order sequence. Enforce monotonicity within each stream only;
        # a future funding timestamp must never make current trades look late.
        self.last_sort_key_by_type: dict[str, tuple] = {}
        self.duplicate_count = 0
        self.late_duplicate_count = 0
        self.late_unique_count = 0
        self.provenance_updates: list[dict] = []

    def push(self, route: str, event: Any, receive_time_ns: int | None = None) -> None:
        identity = event_identity(event)
        receive = int(receive_time_ns or getattr(event, "receive_time_ns", 0) or time.time_ns())
        if identity in self.emitted:
            self.duplicate_count += 1
            self.late_duplicate_count += 1
            self.provenance_updates.append({
                "identity": canonical_json(identity), "route": route,
                "receive_time_ns": receive, "status": "LATE_DUPLICATE",
            })
            return
        pending = self.pending.get(identity)
        if pending is None:
            pending = PendingCanonicalEvent(event, identity, self.clock())
            self.pending[identity] = pending
        else:
            self.duplicate_count += 1
        pending.routes.add(route)
        pending.receive_times_ns[route] = receive

    def release_ready(self, *, force: bool = False) -> list[PendingCanonicalEvent]:
        now = self.clock()
        ready = [
            value for value in self.pending.values()
            if force or now - value.first_arrival_mono >= self.reorder_delay_seconds
        ]
        ready.sort(key=lambda value: event_sort_key(value.event))
        emitted: list[PendingCanonicalEvent] = []
        for value in ready:
            key = event_sort_key(value.event)
            stream = str(value.event.event_type)
            del self.pending[value.identity]
            last_key = self.last_sort_key_by_type.get(stream)
            if last_key is not None and key < last_key:
                self.late_unique_count += 1
                self.provenance_updates.append({
                    "identity": canonical_json(value.identity),
                    "route": ";".join(sorted(value.routes)),
                    "receive_time_ns": min(value.receive_times_ns.values()),
                    "status": "LATE_UNIQUE_DROPPED",
                })
                continue
            self.last_sort_key_by_type[stream] = key
            self.emitted.add(value.identity)
            self.emitted_order.append(value.identity)
            if len(self.emitted_order) > self.dedup_capacity:
                self.emitted.discard(self.emitted_order.popleft())
            emitted.append(value)
        return emitted


def canonical_health(route_states: dict[str, str]) -> str:
    """Return canonical availability independently of unhealthy route repair."""
    healthy = sum(state == "HEALTHY" for state in route_states.values())
    if healthy >= 2:
        return "HEALTHY_REDUNDANT"
    if healthy == 1:
        return "HEALTHY_DEGRADED"
    return "UNAVAILABLE"


class CanonicalWriteAheadLog:
    """Single append-only WAL with atomic committed-sequence watermark."""

    def __init__(self, root: Path, *, sync_interval_ms: float = 100.0) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / "events.jsonl"
        self.commit_path = self.root / "commit.json"
        self.provenance_path = self.root / "provenance_updates.jsonl"
        self.handle = self.path.open("a", encoding="utf-8", buffering=256 * 1024)
        self.provenance_handle = self.provenance_path.open("a", encoding="utf-8", buffering=64 * 1024)
        self.sequence = 0
        self.committed_sequence = 0
        self.last_sync_mono = time.monotonic()
        self.sync_interval = float(sync_interval_ms) / 1_000.0

    def append(self, value: PendingCanonicalEvent) -> int:
        self.sequence += 1
        payload = asdict(value.event)
        if "raw" in payload:
            payload["raw"] = None
        payload["event_class"] = type(value.event).__name__
        identity = canonical_json(value.identity)
        row = {
            "canonical_sequence": self.sequence,
            "event_id": hashlib.sha256(identity.encode()).hexdigest(),
            "source": "BINANCE_USDM_ACTIVE_ACTIVE_CANONICAL",
            "ts_exchange": int(value.event.event_time_ns),
            "ts_receive": min(value.receive_times_ns.values()),
            "ts_canonical_emit": time.time_ns(),
            "route_provenance": sorted(value.routes),
            "route_receive_times_ns": value.receive_times_ns,
            "payload": payload,
        }
        self.handle.write(canonical_json(row) + "\n")
        if time.monotonic() - self.last_sync_mono >= self.sync_interval:
            self.sync()
        return self.sequence

    def append_provenance_updates(self, rows: list[dict]) -> None:
        for row in rows:
            self.provenance_handle.write(canonical_json(row) + "\n")

    def sync(self) -> None:
        self.handle.flush()
        os.fsync(self.handle.fileno())
        self.provenance_handle.flush()
        os.fsync(self.provenance_handle.fileno())
        self.committed_sequence = self.sequence
        contents = canonical_json({
            "committed_sequence": self.committed_sequence,
            "updated_at_ns": time.time_ns(),
        }) + "\n"
        if os.name == "nt":
            # The WAL was fsynced above. A transient partial checkpoint parses
            # as invalid and the consumer waits; Windows cannot reliably
            # replace a checkpoint while another process briefly reads it.
            self.commit_path.write_text(contents, encoding="utf-8")
        else:
            temporary = self.commit_path.with_suffix(".json.tmp")
            temporary.write_text(contents, encoding="utf-8")
            os.replace(temporary, self.commit_path)
        self.last_sync_mono = time.monotonic()

    def close(self) -> None:
        self.sync()
        self.handle.close()
        self.provenance_handle.close()
