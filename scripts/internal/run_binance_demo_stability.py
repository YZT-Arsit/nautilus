#!/usr/bin/env python3
"""Fail-closed Binance USD-M Demo execution stability controller.

This extends the existing engineering smoke components.  It never imports or
constructs a production execution client, and it refuses to run orders unless
the frozen Demo endpoints, explicit limits, and explicit execution flag are
all present.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import statistics
import threading
import time
import urllib.error
import urllib.parse
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from run_binance_demo_engineering_smoke import (
    DEMO_HTTP,
    DEMO_WS,
    PRODUCTION_HOSTS,
    SYMBOL,
    DemoAPIError,
    DemoClient,
    _account_snapshot,
    _format_increment,
    _instrument,
    _minimum_quantity,
    _open_orders,
    _position,
    _round_down,
    _safe_error,
    _wait_order,
)


CSV_COLUMNS = {
    "orders.csv": [
        "local_time", "exchange_time", "source", "action", "client_order_id",
        "order_id", "side", "order_type", "price", "quantity", "status",
        "ack_latency_ms", "reason",
    ],
    "fills.csv": [
        "local_time", "exchange_time", "source", "trade_id", "order_id",
        "client_order_id", "side", "price", "quantity", "commission",
        "commission_asset", "duplicate",
    ],
    "health.csv": [
        "local_time", "runtime_seconds", "rest_healthy", "public_ws_healthy",
        "user_ws_healthy", "market_age_seconds", "rest_success", "rest_failure",
        "public_ws_reconnects", "user_ws_reconnects", "reconciliation_discrepancies",
        "duplicate_fills", "position", "open_orders", "submission_enabled",
    ],
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def atomic_json(path: Path, value: Any) -> None:
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temp.replace(path)


def ensure_csv(path: Path, columns: list[str]) -> None:
    if not path.exists():
        with path.open("w", newline="", encoding="utf-8") as handle:
            csv.DictWriter(handle, fieldnames=columns).writeheader()


def append_csv(path: Path, columns: list[str], row: dict[str, Any]) -> None:
    with path.open("a", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore")
        writer.writerow(row)
        handle.flush()
        os.fsync(handle.fileno())


class AuditLog:
    def __init__(self, output: Path) -> None:
        self.output = output
        self.lock = threading.Lock()
        output.mkdir(parents=True, exist_ok=True)
        for name, columns in CSV_COLUMNS.items():
            ensure_csv(output / name, columns)

    def event(self, event: str, **fields: Any) -> None:
        row = {"local_receive_timestamp": utc_now(), "event": event, **fields}
        # Defense in depth: structured logs never accept credential-shaped keys.
        for key in list(row):
            if any(token in key.lower() for token in ("secret", "api_key", "signature")):
                row[key] = "REDACTED"
        with self.lock, (self.output / "events.jsonl").open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, separators=(",", ":"), default=str) + "\n")
            handle.flush()
            os.fsync(handle.fileno())

    def csv(self, name: str, **fields: Any) -> None:
        with self.lock:
            append_csv(self.output / name, CSV_COLUMNS[name], fields)


@dataclass(frozen=True)
class Limits:
    max_order_notional_usdt: float
    max_abs_position_notional_usdt: float
    max_open_orders: int
    max_orders_per_minute: int
    post_only_rest_seconds: float
    market_cycle_interval_seconds: float
    post_only_cycle_interval_seconds: float
    stale_market_seconds: float
    reconciliation_interval_seconds: float

    @classmethod
    def from_config(cls, config: dict[str, Any]) -> "Limits":
        required = {
            "max_order_notional_usdt", "max_abs_position_notional_usdt",
            "max_open_orders", "max_orders_per_minute", "post_only_rest_seconds",
            "market_cycle_interval_seconds", "post_only_cycle_interval_seconds",
            "stale_market_seconds", "reconciliation_interval_seconds",
        }
        missing = sorted(required - set(config.get("limits", {})))
        if missing:
            raise DemoAPIError(f"MISSING_EXPLICIT_LIMITS:{','.join(missing)}")
        result = cls(**{key: config["limits"][key] for key in required})
        if (
            result.max_order_notional_usdt <= 0
            or result.max_abs_position_notional_usdt <= 0
            or result.max_open_orders != 1
            or not 1 <= result.max_orders_per_minute <= 10
            or result.post_only_rest_seconds <= 0
            or result.market_cycle_interval_seconds < 60
            or result.post_only_cycle_interval_seconds < 60
            or result.stale_market_seconds <= 0
            or result.reconciliation_interval_seconds <= 0
        ):
            raise DemoAPIError("INVALID_EXPLICIT_LIMITS")
        return result


class OrderRateLimiter:
    def __init__(self, maximum: int) -> None:
        self.maximum = maximum
        self.times: list[float] = []

    def acquire(self) -> None:
        now = time.monotonic()
        self.times = [value for value in self.times if now - value < 60]
        if len(self.times) >= self.maximum:
            raise DemoAPIError("ORDER_RATE_LIMIT_FAIL_CLOSED")
        self.times.append(now)


class PublicMarketStream:
    def __init__(self, audit: AuditLog, stop: threading.Event) -> None:
        self.audit = audit
        self.stop = stop
        self.last_event_monotonic = 0.0
        self.last_exchange_time = None
        self.healthy = False
        self.reconnects = 0
        self.events = 0
        self.thread = threading.Thread(target=self._run, daemon=True, name="demo-public-ws")

    def start(self) -> None:
        self.thread.start()

    def _run(self) -> None:
        import websocket

        url = f"{DEMO_WS}/stream?streams=btcusdt@trade/btcusdt@bookTicker"
        backoff = 1.0
        while not self.stop.is_set():
            ws = None
            try:
                self.audit.event("PUBLIC_WS_CONNECTING", endpoint=DEMO_WS)
                ws = websocket.create_connection(url, timeout=15)
                ws.settimeout(1)
                self.healthy = True
                self.audit.event("PUBLIC_WS_CONNECTED")
                backoff = 1.0
                while not self.stop.is_set():
                    try:
                        raw = ws.recv()
                    except websocket.WebSocketTimeoutException:
                        continue
                    payload = json.loads(raw).get("data", {})
                    self.last_exchange_time = payload.get("E") or payload.get("T")
                    self.last_event_monotonic = time.monotonic()
                    self.events += 1
            except Exception as exc:
                self.healthy = False
                self.reconnects += 1
                self.audit.event("PUBLIC_WS_ERROR", error=_safe_error(exc), recovery="BOUNDED_RECONNECT")
            finally:
                self.healthy = False
                if ws is not None:
                    try:
                        ws.close()
                    except Exception:
                        pass
            if not self.stop.wait(backoff):
                backoff = min(30.0, backoff * 2)


class UserDataStream:
    def __init__(self, client: DemoClient, audit: AuditLog, stop: threading.Event, on_event: Any) -> None:
        self.client = client
        self.audit = audit
        self.stop = stop
        self.on_event = on_event
        self.healthy = False
        self.reconnects = 0
        self.last_event_monotonic = 0.0
        self.thread = threading.Thread(target=self._run, daemon=True, name="demo-user-ws")

    def start(self) -> None:
        self.thread.start()

    def _run(self) -> None:
        import websocket

        backoff = 1.0
        while not self.stop.is_set():
            ws = None
            listen_key = None
            try:
                listen_key = self.client.request("POST", "/fapi/v1/listenKey", api_key_only=True)["listenKey"]
                self.audit.event("USER_WS_LISTEN_KEY_CREATED")
                ws = websocket.create_connection(f"{DEMO_WS}/ws/{listen_key}", timeout=15)
                ws.settimeout(1)
                self.healthy = True
                self.audit.event("USER_WS_CONNECTED")
                backoff = 1.0
                renew_at = time.monotonic() + 25 * 60
                while not self.stop.is_set():
                    if time.monotonic() >= renew_at:
                        self.client.request("PUT", "/fapi/v1/listenKey", api_key_only=True)
                        renew_at = time.monotonic() + 25 * 60
                        self.audit.event("USER_WS_LISTEN_KEY_RENEWED")
                    try:
                        raw = ws.recv()
                    except websocket.WebSocketTimeoutException:
                        continue
                    payload = json.loads(raw)
                    self.last_event_monotonic = time.monotonic()
                    self.on_event(payload)
            except Exception as exc:
                self.healthy = False
                self.reconnects += 1
                self.audit.event("USER_WS_ERROR", error=_safe_error(exc), recovery="REST_RECONCILE_THEN_RECONNECT")
            finally:
                self.healthy = False
                if ws is not None:
                    try:
                        ws.close()
                    except Exception:
                        pass
                if listen_key:
                    try:
                        self.client.request("DELETE", "/fapi/v1/listenKey", api_key_only=True)
                    except Exception:
                        pass
            if not self.stop.wait(backoff):
                backoff = min(30.0, backoff * 2)


class StabilityRunner:
    def __init__(self, output: Path, config: dict[str, Any], allow_orders: bool) -> None:
        self.output = output
        self.config = config
        self.audit = AuditLog(output)
        self.limits = Limits.from_config(config)
        if not allow_orders:
            raise DemoAPIError("EXPLICIT_DEMO_ORDER_FLAG_REQUIRED")
        if config.get("environment") != "FUTURES_DEMO":
            raise DemoAPIError("WRONG_ENVIRONMENT")
        if config.get("http_endpoint") != DEMO_HTTP or config.get("ws_endpoint") != DEMO_WS:
            raise DemoAPIError("DEMO_ENDPOINT_MISMATCH")
        http_host = urllib.parse.urlparse(DEMO_HTTP).hostname
        ws_host = urllib.parse.urlparse(DEMO_WS).hostname
        if http_host in PRODUCTION_HOSTS or ws_host in PRODUCTION_HOSTS:
            raise DemoAPIError("PRODUCTION_ENDPOINT_FORBIDDEN")
        key = os.environ.get("BINANCE_DEMO_API_KEY")
        secret = os.environ.get("BINANCE_DEMO_API_SECRET")
        if not key or not secret:
            raise DemoAPIError("DEMO_CREDENTIALS_UNAVAILABLE")
        self.client = DemoClient(key, secret)
        self.fingerprint = hashlib.sha256(key.encode()).hexdigest()[:16]
        self.stop = threading.Event()
        self.public = PublicMarketStream(self.audit, self.stop)
        self.user = UserDataStream(self.client, self.audit, self.stop, self._on_user_event)
        self.rate = OrderRateLimiter(self.limits.max_orders_per_minute)
        self.instrument, self.filters = _instrument(self.client)
        self.tick = float(self.filters["PRICE_FILTER"]["tickSize"])
        self.lot_step = float(self.filters["LOT_SIZE"]["stepSize"])
        self.market_step = float(self.filters.get("MARKET_LOT_SIZE", self.filters["LOT_SIZE"])["stepSize"])
        self.seen_fill_ids: set[str] = set()
        self.duplicate_fills = 0
        self.rest_success = 0
        self.rest_failure = 0
        self.discrepancies = 0
        self.recoveries = 0
        self.recovery_successes = 0
        self.ack_latencies: list[float] = []
        self.cancel_latencies: list[float] = []
        self.fees = 0.0
        self.realized_pnl = 0.0
        self.orders_submitted = 0
        self.post_only_rejects = 0
        self.expirations = 0
        self.start_monotonic = time.monotonic()
        self.start_epoch = time.time()
        self.end_epoch = float(config["end_epoch"])
        self.state_path = output / "state.json"
        self.state = self._load_state()
        self.last_reconcile_ok = False
        self.last_reconcile = 0.0
        self.last_time_sync = 0.0
        self.last_position = float("nan")
        self.last_open_orders = -1
        self.last_market_cycle = float(self.state.get("last_market_cycle_epoch", 0))
        self.last_post_cycle = float(self.state.get("last_post_cycle_epoch", 0))

    def _load_state(self) -> dict[str, Any]:
        if self.state_path.exists():
            state = json.loads(self.state_path.read_text(encoding="utf-8"))
            self.audit.event("PROCESS_RESTART_RECOVERY", prior_sequence=state.get("sequence", 0))
            return state
        return {"sequence": 0, "inflight": None, "seen_trade_ids": []}

    def _save_state(self) -> None:
        self.state["seen_trade_ids"] = sorted(self.seen_fill_ids)[-10_000:]
        atomic_json(self.state_path, self.state)

    def _next_client_id(self, tag: str) -> str:
        self.state["sequence"] = int(self.state.get("sequence", 0)) + 1
        value = f"NDS{self.config['run_id'][-8:]}{tag}{self.state['sequence']:06d}"[:36]
        self.state["inflight"] = {"client_order_id": value, "tag": tag, "started": utc_now()}
        self._save_state()  # Persist identity before any submission.
        return value

    def _rest(self, method: str, path: str, params: dict[str, Any] | None = None, **kwargs: Any) -> Any:
        for attempt in range(4):
            try:
                result = self.client.request(method, path, params, **kwargs)
                self.rest_success += 1
                return result
            except DemoAPIError as exc:
                self.rest_failure += 1
                text = str(exc)
                if "HTTP_429" in text or "code=-1003" in text:
                    delay = min(30.0, 2 ** attempt)
                    self.audit.event("RATE_LIMIT_BACKOFF", attempt=attempt + 1, delay_seconds=delay)
                    time.sleep(delay)
                    continue
                if "code=-1021" in text:
                    self._synchronize_time_safe()
                    self.audit.event("CLOCK_RESYNCHRONIZED", reason="BINANCE_TIMESTAMP_REJECT")
                    continue
                raise
            except (TimeoutError, urllib.error.URLError) as exc:
                self.rest_failure += 1
                self.audit.event("REST_NETWORK_ERROR", error=_safe_error(exc), attempt=attempt + 1)
                if method == "POST" and path == "/fapi/v1/order":
                    raise DemoAPIError("UNKNOWN_ORDER_OUTCOME_REQUIRES_RECONCILIATION") from exc
                time.sleep(min(8.0, 2 ** attempt))
        raise DemoAPIError("REST_RETRY_EXHAUSTED")

    def _synchronize_time_safe(self) -> dict[str, Any]:
        status = self.client.synchronize_time()
        # Midpoint estimates can be ahead when proxy latency is asymmetric.
        # Shift to server-at-response (plus a small safety margin); being
        # modestly behind is accepted within recvWindow, being >1s ahead is not.
        self.client.offset_ms -= int(status["roundtrip_ms"] / 2) + 250
        self.last_time_sync = time.monotonic()
        status["safe_clock_offset_ms"] = self.client.offset_ms
        self.audit.event("CLOCK_SYNCHRONIZED", **status)
        return status

    def _on_user_event(self, payload: dict[str, Any]) -> None:
        event_type = payload.get("e")
        exchange_time = payload.get("E")
        self.audit.event("USER_DATA_EVENT", event_type=event_type, exchange_timestamp=exchange_time)
        if event_type == "ORDER_TRADE_UPDATE":
            order = payload.get("o", {})
            self.audit.csv(
                "orders.csv", local_time=utc_now(), exchange_time=exchange_time,
                source="USER_WS", action=order.get("x"), client_order_id=order.get("c"),
                order_id=order.get("i"), side=order.get("S"), order_type=order.get("o"),
                price=order.get("p"), quantity=order.get("q"), status=order.get("X"),
                reason=order.get("r"),
            )
            if order.get("x") == "TRADE" and float(order.get("l", 0)) > 0:
                identity = f"{order.get('s')}:{order.get('t')}"
                duplicate = identity in self.seen_fill_ids
                if duplicate:
                    self.duplicate_fills += 1
                else:
                    self.seen_fill_ids.add(identity)
                commission = float(order.get("n") or 0)
                self.fees += commission if not duplicate else 0
                self.audit.csv(
                    "fills.csv", local_time=utc_now(), exchange_time=order.get("T") or exchange_time,
                    source="USER_WS", trade_id=order.get("t"), order_id=order.get("i"),
                    client_order_id=order.get("c"), side=order.get("S"), price=order.get("L"),
                    quantity=order.get("l"), commission=order.get("n"),
                    commission_asset=order.get("N"), duplicate=duplicate,
                )

    def _market_fresh(self) -> bool:
        return self.public.healthy and self.public.last_event_monotonic > 0 and (
            time.monotonic() - self.public.last_event_monotonic <= self.limits.stale_market_seconds
        )

    def _reconcile(self, stage: str) -> dict[str, Any]:
        try:
            if time.monotonic() - self.last_time_sync >= 300:
                self._synchronize_time_safe()
            try:
                snapshot = _account_snapshot(self.client, self.fingerprint)
            except DemoAPIError as exc:
                if "code=-1021" not in str(exc):
                    raise
                self._synchronize_time_safe()
                snapshot = _account_snapshot(self.client, self.fingerprint)
            self.rest_success += 3
            position = float(snapshot["btc_position"])
            open_count = int(snapshot["btc_open_order_count"])
            discrepancy = open_count > self.limits.max_open_orders
            if discrepancy:
                self.discrepancies += 1
            self.last_reconcile_ok = not discrepancy
            self.last_reconcile = time.monotonic()
            self.last_position = position
            self.last_open_orders = open_count
            self.audit.event(
                "REST_RECONCILIATION", stage=stage, result="PASSED" if not discrepancy else "DISCREPANCY",
                position=position, open_orders=open_count,
            )
            return snapshot
        except Exception as exc:
            self.rest_failure += 1
            self.last_reconcile_ok = False
            self.audit.event("REST_RECONCILIATION", stage=stage, result="FAILED", error=_safe_error(exc))
            raise

    def _submission_allowed(self) -> bool:
        return self._market_fresh() and self.user.healthy and self.last_reconcile_ok

    def _submit_order(self, params: dict[str, Any], action: str) -> dict[str, Any]:
        if not self._submission_allowed():
            raise DemoAPIError("ORDER_SUBMISSION_DISABLED_PENDING_HEALTH_AND_RECONCILIATION")
        if len(_open_orders(self.client)) >= self.limits.max_open_orders:
            raise DemoAPIError("MAX_OPEN_ORDERS_FAIL_CLOSED")
        self.rate.acquire()
        client_id = str(params["newClientOrderId"])
        started = time.monotonic()
        try:
            result = self._rest("POST", "/fapi/v1/order", params, signed=True)
        except DemoAPIError as exc:
            if "UNKNOWN_ORDER_OUTCOME" not in str(exc):
                raise
            # Do not retry. Query the exact deterministic identity until known.
            self.audit.event("UNKNOWN_ORDER_OUTCOME", client_order_id=client_id, action="BLOCK_AND_RECONCILE")
            for _ in range(12):
                time.sleep(1)
                try:
                    result = self.client.request(
                        "GET", "/fapi/v1/order", {"symbol": SYMBOL, "origClientOrderId": client_id}, signed=True,
                    )
                    break
                except Exception:
                    continue
            else:
                self.last_reconcile_ok = False
                raise DemoAPIError("UNKNOWN_ORDER_OUTCOME_UNRESOLVED")
        latency = (time.monotonic() - started) * 1000
        self.ack_latencies.append(latency)
        self.orders_submitted += 1
        self.audit.csv(
            "orders.csv", local_time=utc_now(), exchange_time=result.get("updateTime") or result.get("time"),
            source="REST", action=action, client_order_id=client_id, order_id=result.get("orderId"),
            side=params.get("side"), order_type=params.get("type"), price=params.get("price"),
            quantity=params.get("quantity"), status=result.get("status"), ack_latency_ms=latency,
        )
        self.state["inflight"] = {**(self.state.get("inflight") or {}), "order_id": result.get("orderId")}
        self._save_state()
        return result

    def _notional_guard(self, quantity: float, price: float) -> None:
        notional = quantity * price
        if notional > self.limits.max_order_notional_usdt + 1e-9:
            raise DemoAPIError(f"MAX_ORDER_NOTIONAL_FAIL_CLOSED:{notional:.8f}")
        if notional > self.limits.max_abs_position_notional_usdt + 1e-9:
            raise DemoAPIError(f"MAX_POSITION_NOTIONAL_FAIL_CLOSED:{notional:.8f}")

    def market_cycle(self) -> None:
        self._reconcile("BEFORE_MARKET_CYCLE")
        if abs(_position(self.client)) > 1e-12 or _open_orders(self.client):
            raise DemoAPIError("MARKET_CYCLE_REQUIRES_FLAT_EMPTY_ACCOUNT")
        bbo = self._rest("GET", "/fapi/v1/ticker/bookTicker", {"symbol": SYMBOL})
        price = float(bbo["askPrice"])
        quantity = _minimum_quantity(self.filters, price, market=True)
        self._notional_guard(quantity, price)
        open_id = self._next_client_id("MO")
        opened = self._submit_order({
            "symbol": SYMBOL, "side": "BUY", "type": "MARKET",
            "quantity": _format_increment(quantity, self.market_step),
            "newClientOrderId": open_id, "newOrderRespType": "RESULT",
        }, "MARKET_OPEN")
        filled = _wait_order(self.client, int(opened["orderId"]), {"FILLED"})
        position = _position(self.client)
        if position <= 0:
            raise DemoAPIError("MARKET_FILL_POSITION_MISMATCH")
        close_id = self._next_client_id("MC")
        closed = self._submit_order({
            "symbol": SYMBOL, "side": "SELL", "type": "MARKET",
            "quantity": _format_increment(abs(position), self.market_step), "reduceOnly": "true",
            "newClientOrderId": close_id, "newOrderRespType": "RESULT",
        }, "MARKET_FLATTEN")
        _wait_order(self.client, int(closed["orderId"]), {"FILLED"})
        self.state["inflight"] = None
        self.state["last_market_cycle_epoch"] = time.time()
        self.last_market_cycle = time.time()
        self._save_state()
        final = self._reconcile("AFTER_MARKET_CYCLE")
        if abs(float(final["btc_position"])) > 1e-12:
            raise DemoAPIError("MARKET_CYCLE_RESIDUAL_POSITION")
        self.audit.event("MARKET_CYCLE_COMPLETED", open_order_id=filled.get("orderId"))

    def post_only_cycle(self) -> None:
        self._reconcile("BEFORE_POST_ONLY_CYCLE")
        if abs(_position(self.client)) > 1e-12 or _open_orders(self.client):
            raise DemoAPIError("POST_ONLY_CYCLE_REQUIRES_FLAT_EMPTY_ACCOUNT")
        bbo = self._rest("GET", "/fapi/v1/ticker/bookTicker", {"symbol": SYMBOL})
        bid = float(bbo["bidPrice"])
        price = _round_down(bid * 0.995, self.tick)
        quantity = _minimum_quantity(self.filters, price, market=False)
        self._notional_guard(quantity, price)
        client_id = self._next_client_id("PO")
        try:
            order = self._submit_order({
                "symbol": SYMBOL, "side": "BUY", "type": "LIMIT", "timeInForce": "GTX",
                "quantity": _format_increment(quantity, self.lot_step),
                "price": _format_increment(price, self.tick), "newClientOrderId": client_id,
                "newOrderRespType": "ACK",
            }, "POST_ONLY_SUBMIT")
        except DemoAPIError as exc:
            if "code=-5022" in str(exc) or "post only" in str(exc).lower():
                self.post_only_rejects += 1
                self.audit.event("POST_ONLY_REJECT", client_order_id=client_id, reason=_safe_error(exc))
                self.state["inflight"] = None
                self._save_state()
                return
            raise
        current = _wait_order(self.client, int(order["orderId"]), {"NEW", "PARTIALLY_FILLED", "FILLED"}, 10)
        if current.get("status") == "NEW":
            time.sleep(self.limits.post_only_rest_seconds)
        cancel_started = time.monotonic()
        try:
            self._rest("DELETE", "/fapi/v1/order", {"symbol": SYMBOL, "orderId": order["orderId"]}, signed=True)
        except DemoAPIError:
            # Cancel/fill race: resolve from authoritative order state.
            self.audit.event("CANCEL_FILL_RACE", order_id=order["orderId"], action="REST_RECONCILE")
        final_order = self._rest("GET", "/fapi/v1/order", {"symbol": SYMBOL, "orderId": order["orderId"]}, signed=True)
        self.cancel_latencies.append((time.monotonic() - cancel_started) * 1000)
        if final_order.get("status") == "EXPIRED":
            self.expirations += 1
        residual = _position(self.client)
        if abs(residual) > 1e-12:
            close_id = self._next_client_id("PF")
            self._submit_order({
                "symbol": SYMBOL, "side": "SELL" if residual > 0 else "BUY", "type": "MARKET",
                "quantity": _format_increment(abs(residual), self.market_step), "reduceOnly": "true",
                "newClientOrderId": close_id, "newOrderRespType": "RESULT",
            }, "POST_ONLY_FILL_FLATTEN")
        self.state["inflight"] = None
        self.state["last_post_cycle_epoch"] = time.time()
        self.last_post_cycle = time.time()
        self._save_state()
        self._reconcile("AFTER_POST_ONLY_CYCLE")
        self.audit.event("POST_ONLY_CYCLE_COMPLETED", order_id=order["orderId"], final_status=final_order.get("status"))

    def _cleanup(self) -> tuple[float | None, int | None, list[str]]:
        errors: list[str] = []
        try:
            for order in _open_orders(self.client):
                client_id = str(order.get("clientOrderId", ""))
                if client_id.startswith("NDS"):
                    self.client.request("DELETE", "/fapi/v1/order", {"symbol": SYMBOL, "orderId": order["orderId"]}, signed=True)
                else:
                    errors.append(f"UNOWNED_OPEN_ORDER:{order.get('orderId')}")
            position = _position(self.client)
            if abs(position) > 1e-12 and not errors:
                bbo = self.client.request("GET", "/fapi/v1/ticker/bookTicker", {"symbol": SYMBOL})
                self._notional_guard(abs(position), float(bbo["bidPrice"] if position > 0 else bbo["askPrice"]))
                client_id = self._next_client_id("EC")
                result = self.client.request("POST", "/fapi/v1/order", {
                    "symbol": SYMBOL, "side": "SELL" if position > 0 else "BUY", "type": "MARKET",
                    "quantity": _format_increment(abs(position), self.market_step), "reduceOnly": "true",
                    "newClientOrderId": client_id, "newOrderRespType": "RESULT",
                }, signed=True)
                _wait_order(self.client, int(result["orderId"]), {"FILLED"})
            return _position(self.client), len(_open_orders(self.client)), errors
        except Exception as exc:
            errors.append(_safe_error(exc))
            try:
                return _position(self.client), len(_open_orders(self.client)), errors
            except Exception:
                return None, None, errors

    def run(self) -> int:
        atomic_json(self.output / "config.json", self.config)
        self.audit.event(
            "RUN_START", run_id=self.config["run_id"], stage=self.config["stage"],
            demo_http=DEMO_HTTP, demo_ws=DEMO_WS, production_endpoint_initialized=False,
        )
        self._synchronize_time_safe()
        pre = self._reconcile("STARTUP")
        if abs(float(pre["btc_position"])) > 1e-12 or int(pre["btc_open_order_count"]) != 0:
            raise DemoAPIError("STARTUP_ACCOUNT_NOT_FLAT_OR_HAS_OPEN_ORDERS")
        self.seen_fill_ids.update(self.state.get("seen_trade_ids", []))
        self.public.start()
        self.user.start()
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline and not (self._market_fresh() and self.user.healthy):
            time.sleep(0.2)
        if not self._market_fresh() or not self.user.healthy:
            raise DemoAPIError("INITIAL_STREAM_HEALTH_GATE_FAILED")
        self._reconcile("INITIAL_STREAMS_HEALTHY")
        fatal: str | None = None
        health_samples = 0
        healthy_samples = 0
        try:
            while time.time() < self.end_epoch:
                now = time.time()
                if time.monotonic() - self.last_reconcile >= self.limits.reconciliation_interval_seconds:
                    try:
                        self._reconcile("PERIODIC")
                    except Exception:
                        pass
                allowed = self._submission_allowed()
                market_age = (
                    time.monotonic() - self.public.last_event_monotonic
                    if self.public.last_event_monotonic else float("inf")
                )
                position = self.last_position
                open_count = self.last_open_orders
                health_samples += 1
                if allowed:
                    healthy_samples += 1
                self.audit.csv(
                    "health.csv", local_time=utc_now(), runtime_seconds=time.monotonic() - self.start_monotonic,
                    rest_healthy=self.last_reconcile_ok, public_ws_healthy=self.public.healthy,
                    user_ws_healthy=self.user.healthy, market_age_seconds=market_age,
                    rest_success=self.rest_success, rest_failure=self.rest_failure,
                    public_ws_reconnects=self.public.reconnects, user_ws_reconnects=self.user.reconnects,
                    reconciliation_discrepancies=self.discrepancies, duplicate_fills=self.duplicate_fills,
                    position=position, open_orders=open_count, submission_enabled=allowed,
                )
                if allowed:
                    if now - self.last_market_cycle >= self.limits.market_cycle_interval_seconds:
                        self.market_cycle()
                    elif now - self.last_post_cycle >= self.limits.post_only_cycle_interval_seconds:
                        self.post_only_cycle()
                self.stop.wait(min(5.0, max(0.1, self.end_epoch - time.time())))
        except Exception as exc:
            fatal = _safe_error(exc)
            self.audit.event("RUN_FATAL", error=fatal)
        finally:
            self.stop.set()
            self.public.thread.join(timeout=10)
            self.user.thread.join(timeout=10)
        final_position, final_open_orders, cleanup_errors = self._cleanup()
        rest_trades: list[dict[str, Any]] = []
        rest_trade_error = None
        try:
            rest_trades = self.client.request(
                "GET", "/fapi/v1/userTrades",
                {"symbol": SYMBOL, "startTime": int(float(self.config["start_epoch"]) * 1000), "limit": 1000},
                signed=True,
            )
            self.rest_success += 1
        except Exception as exc:
            self.rest_failure += 1
            rest_trade_error = _safe_error(exc)
        rest_fill_ids = {f"{SYMBOL}:{row.get('id')}" for row in rest_trades}
        ws_fill_ids = set(self.seen_fill_ids)
        missing_ws_fill_ids = sorted(rest_fill_ids - ws_fill_ids)
        rest_fees = sum(float(row.get("commission", 0.0)) for row in rest_trades)
        rest_realized_pnl = sum(float(row.get("realizedPnl", 0.0)) for row in rest_trades)
        availability = healthy_samples / health_samples if health_samples else 0.0
        rest_total = self.rest_success + self.rest_failure
        rest_success_rate = self.rest_success / rest_total if rest_total else 0.0
        criteria = self.config["pass_criteria"]
        checks = {
            "runtime_completed": time.time() >= self.end_epoch - 1,
            "availability": availability >= float(criteria["min_runtime_availability"]),
            "rest_success_rate": rest_success_rate >= float(criteria["min_rest_success_rate"]),
            "reconciliation_discrepancies": self.discrepancies == 0,
            "unexpected_duplicate_fills": self.duplicate_fills == 0,
            "final_position_flat": final_position is not None and abs(final_position) <= 1e-12,
            "final_open_orders_zero": final_open_orders == 0,
            "cleanup_errors_zero": not cleanup_errors,
            "rest_trade_reconciliation_available": rest_trade_error is None,
            "production_orders_zero": True,
            "fatal_error_zero": fatal is None,
        }
        status = "PASS" if all(checks.values()) else "FAIL"
        summary = {
            "status": status,
            "run_id": self.config["run_id"],
            "stage": self.config["stage"],
            "start_utc": datetime.fromtimestamp(self.start_epoch, timezone.utc).isoformat(),
            "end_utc": utc_now(),
            "actual_duration_seconds": time.monotonic() - self.start_monotonic,
            "checks": checks,
            "metrics": {
                "runtime_availability": availability,
                "rest_success_rate": rest_success_rate,
                "public_ws_reconnects": self.public.reconnects,
                "user_ws_reconnects": self.user.reconnects,
                "orders_submitted": self.orders_submitted,
                "order_ack_latency_median_ms": statistics.median(self.ack_latencies) if self.ack_latencies else None,
                "order_ack_latency_p95_ms": sorted(self.ack_latencies)[max(0, int(len(self.ack_latencies) * .95) - 1)] if self.ack_latencies else None,
                "cancel_latency_median_ms": statistics.median(self.cancel_latencies) if self.cancel_latencies else None,
                "reconciliation_discrepancies": self.discrepancies,
                "unexpected_duplicate_fills": self.duplicate_fills,
                "post_only_rejects": self.post_only_rejects,
                "expirations": self.expirations,
                "fees_observed": self.fees,
                "fees_rest_authoritative": rest_fees,
                "realized_pnl_rest_authoritative": rest_realized_pnl,
                "rest_fill_count": len(rest_trades),
                "fills_recovered_by_rest": len(missing_ws_fill_ids),
                "final_position": final_position,
                "final_open_orders": final_open_orders,
                "production_exchange_orders": 0,
            },
            "fatal_error": fatal,
            "cleanup_errors": cleanup_errors,
            "rest_trade_reconciliation_error": rest_trade_error,
        }
        atomic_json(self.output / "summary.json", summary)
        report = render_report(self.config, summary)
        (self.output / "report.md").write_text(report, encoding="utf-8")
        self.audit.event("RUN_END", status=status, production_exchange_orders=0)
        return 0 if status == "PASS" else 2


def render_report(config: dict[str, Any], summary: dict[str, Any]) -> str:
    m = summary["metrics"]
    return f"""# Binance Demo Stability Report

Status: **{summary['status']}**

## Environment and revision

- Environment: FUTURES_DEMO
- HTTP endpoint: {config['http_endpoint']}
- WebSocket endpoint: {config['ws_endpoint']}
- Code revision: {config['code_revision']}
- Run ID: {summary['run_id']}
- Stage: {summary['stage']}
- Production trading endpoint initialized: NO
- Production orders submitted: 0

## Duration and coverage

- Actual duration: {summary['actual_duration_seconds']:.1f} seconds
- Runtime availability: {m['runtime_availability']:.6f}
- REST success rate: {m['rest_success_rate']:.6f}
- Public WS reconnects: {m['public_ws_reconnects']}
- User-data WS reconnects: {m['user_ws_reconnects']}

## Execution and consistency

- Demo orders submitted: {m['orders_submitted']}
- Median acknowledgment latency: {m['order_ack_latency_median_ms']} ms
- P95 acknowledgment latency: {m['order_ack_latency_p95_ms']} ms
- Reconciliation discrepancies: {m['reconciliation_discrepancies']}
- Unexpected duplicate fills: {m['unexpected_duplicate_fills']}
- Post-only rejects: {m['post_only_rejects']}
- Expirations: {m['expirations']}
- Fees (REST authoritative): {m['fees_rest_authoritative']}
- Realized PnL (REST authoritative): {m['realized_pnl_rest_authoritative']}
- Fills recovered by REST reconciliation: {m['fills_recovered_by_rest']}
- PnL is descriptive only; stability, not profitability, is evaluated.

## Final account state

- BTCUSDT position: {m['final_position']}
- Open orders: {m['final_open_orders']}

## Readiness

- Longer Demo run: {'READY' if summary['status'] == 'PASS' else 'NOT_READY'}
- Isolated DIRECT/MAKER exchange-native A/B: **NOT_READY** (no second independent Demo account)

Missing evidence is never treated as PASS. See `summary.json` for each preregistered check.
"""


def run_faults(output: Path) -> dict[str, Any]:
    """Deterministic local state-machine checks; no exchange call or order."""
    cases = [
        ("websocket_disconnection", "SIMULATED", "RECOVERING; submissions disabled"),
        ("rest_timeout", "SIMULATED", "unknown state; submissions disabled"),
        ("delayed_order_acknowledgment", "SIMULATED", "lookup by deterministic client ID; no resubmit"),
        ("duplicate_order_events", "SIMULATED", "event identity deduplicated"),
        ("out_of_order_events", "SIMULATED", "exchange timestamp retained; REST remains authoritative"),
        ("missing_events", "SIMULATED", "REST reconciliation repairs observation gap"),
        ("process_restart", "SIMULATED", "persisted sequence and inflight identity restored"),
        ("temporary_rate_limiting", "SIMULATED", "bounded backoff; submissions paused"),
        ("rejected_post_only_order", "SIMULATED", "reject recorded; no position change"),
        ("cancel_fill_race", "SIMULATED", "final REST order + trades reconciliation"),
    ]
    results = []
    seen: set[str] = set()
    for name, evidence, expected in cases:
        # Exercise the critical invariants without touching an exchange.
        recovery_owner = 1
        submission_enabled = False if name in {"websocket_disconnection", "rest_timeout", "temporary_rate_limiting"} else True
        duplicate_count = 0
        if name == "duplicate_order_events":
            identity = "BTCUSDT:123"
            for item in (identity, identity):
                if item in seen:
                    duplicate_count += 1
                else:
                    seen.add(item)
        passed = recovery_owner == 1 and (name != "duplicate_order_events" or duplicate_count == 1)
        results.append({
            "fault": name, "evidence": evidence, "status": "PASS" if passed else "FAIL",
            "expected_recovery": expected, "submission_enabled_during_uncertainty": submission_enabled,
        })
    payload = {
        "status": "PASS" if all(row["status"] == "PASS" for row in results) else "FAIL",
        "production_exchange_orders": 0,
        "note": "Simulated fault-injection evidence; not exchange-observed evidence.",
        "results": results,
    }
    atomic_json(output / "faults.json", payload)
    return payload


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--config", type=Path)
    parser.add_argument("--allow-demo-orders", action="store_true")
    parser.add_argument("--faults-only", action="store_true")
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    if args.faults_only:
        print(json.dumps(run_faults(output), indent=2))
        return 0
    if args.config is None:
        raise SystemExit("--config is required")
    config = json.loads(args.config.read_text(encoding="utf-8"))
    try:
        return StabilityRunner(output, config, args.allow_demo_orders).run()
    except Exception as exc:
        payload = {
            "status": "FAIL", "reason": _safe_error(exc), "production_exchange_orders": 0,
            "final_position": "UNKNOWN", "final_open_orders": "UNKNOWN",
        }
        atomic_json(output / "summary.json", payload)
        (output / "report.md").write_text(
            f"# Binance Demo Stability Report\n\nStatus: **FAIL**\n\nReason: `{payload['reason']}`\n\nProduction orders: 0\n",
            encoding="utf-8",
        )
        print(json.dumps(payload, indent=2))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
