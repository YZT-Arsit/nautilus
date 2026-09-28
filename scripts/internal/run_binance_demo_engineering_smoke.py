#!/usr/bin/env python3
"""
Run sequential order-path engineering tests on one Binance USD-M Demo account.

This is not a strategy or performance experiment. The script refuses every
host except the frozen Demo host and requires an explicit execution flag.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import hmac
import json
import os
import ssl
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from decimal import ROUND_DOWN
from decimal import ROUND_UP
from decimal import Decimal
from pathlib import Path
from typing import Any

import pandas as pd


DEMO_HTTP = "https://demo-fapi.binance.com"
DEMO_WS = "wss://demo-fstream.binance.com"
PRODUCTION_HOSTS = {"fapi.binance.com", "fstream.binance.com", "ws-fapi.binance.com"}
SYMBOL = "BTCUSDT"


class DemoAPIError(RuntimeError):
    pass


def _write_csv(path: Path, rows: list[dict], columns: list[str] | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows, columns=columns).to_csv(path, index=False)


def _safe_error(exc: BaseException) -> str:
    text = str(exc)
    return text[:1_000]


class DemoClient:
    def __init__(self, api_key: str, api_secret: str) -> None:
        parsed = urllib.parse.urlparse(DEMO_HTTP)
        if parsed.scheme != "https" or parsed.hostname != "demo-fapi.binance.com":
            raise DemoAPIError("DEMO_ENDPOINT_IDENTITY_INVALID")
        if parsed.hostname in PRODUCTION_HOSTS:
            raise DemoAPIError("PRODUCTION_ENDPOINT_FORBIDDEN")
        self.api_key = api_key
        self.api_secret = api_secret.encode()
        self.offset_ms = 0

    def request(
        self,
        method: str,
        path: str,
        params: dict[str, Any] | None = None,
        *,
        signed: bool = False,
        api_key_only: bool = False,
    ) -> Any:
        values = dict(params or {})
        if signed:
            values.setdefault("recvWindow", 5_000)
            values["timestamp"] = int(time.time() * 1_000) + self.offset_ms
            query = urllib.parse.urlencode(values)
            signature = hmac.new(self.api_secret, query.encode(), hashlib.sha256).hexdigest()
            query = f"{query}&signature={signature}"
        else:
            query = urllib.parse.urlencode(values)
        url = f"{DEMO_HTTP}{path}" + (f"?{query}" if query else "")
        headers = {"User-Agent": "nautilus-demo-engineering/1"}
        if signed or api_key_only:
            headers["X-MBX-APIKEY"] = self.api_key
        request = urllib.request.Request(url, headers=headers, method=method)  # noqa: S310 frozen HTTPS host
        try:
            with urllib.request.urlopen(  # noqa: S310 frozen HTTPS host
                request,
                timeout=20,
                context=ssl.create_default_context(),
            ) as response:
                body = response.read()
                return json.loads(body) if body else {}
        except urllib.error.HTTPError as exc:
            body = exc.read().decode(errors="replace")
            try:
                payload = json.loads(body)
                raise DemoAPIError(f"HTTP_{exc.code}: code={payload.get('code')} msg={payload.get('msg')}") from None
            except json.JSONDecodeError:
                raise DemoAPIError(f"HTTP_{exc.code}: {body[:500]}") from None

    def synchronize_time(self) -> dict[str, Any]:
        before = int(time.time() * 1_000)
        payload = self.request("GET", "/fapi/v1/time")
        after = int(time.time() * 1_000)
        server = int(payload["serverTime"])
        self.offset_ms = server - (before + after) // 2
        return {"server_time_ms": server, "clock_offset_ms": self.offset_ms, "roundtrip_ms": after - before}


class UserDataCollector:
    def __init__(self, listen_key: str) -> None:
        self.listen_key = listen_key
        self.events: list[dict[str, Any]] = []
        self.errors: list[str] = []
        self.stop = threading.Event()
        self.ready = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True, name="demo-user-data")

    def _run(self) -> None:
        try:
            import websocket

            ws = websocket.create_connection(f"{DEMO_WS}/ws/{self.listen_key}", timeout=10)
            ws.settimeout(1)
            self.ready.set()
            try:
                while not self.stop.is_set():
                    try:
                        message = ws.recv()
                    except websocket.WebSocketTimeoutException:
                        continue
                    if not message:
                        continue
                    payload = json.loads(message)
                    event_type = payload.get("e")
                    if event_type in {"ORDER_TRADE_UPDATE", "ACCOUNT_UPDATE", "listenKeyExpired"}:
                        self.events.append(payload)
            finally:
                ws.close()
        except Exception as exc:  # transport is validated by downstream event requirements
            self.errors.append(_safe_error(exc))
            self.ready.set()

    def start(self) -> None:
        self.thread.start()
        if not self.ready.wait(15) or self.errors:
            raise DemoAPIError(f"DEMO_USER_DATA_STREAM_UNAVAILABLE: {self.errors}")

    def close(self) -> None:
        self.stop.set()
        self.thread.join(timeout=5)


def _round_up(value: float, increment: float) -> float:
    unit = Decimal(str(increment))
    return float((Decimal(str(value)) / unit).to_integral_value(rounding=ROUND_UP) * unit)


def _round_down(value: float, increment: float) -> float:
    unit = Decimal(str(increment))
    return float((Decimal(str(value)) / unit).to_integral_value(rounding=ROUND_DOWN) * unit)


def _instrument(client: DemoClient) -> tuple[dict[str, Any], dict[str, Any]]:
    exchange = client.request("GET", "/fapi/v1/exchangeInfo")
    symbol = next((row for row in exchange["symbols"] if row["symbol"] == SYMBOL), None)
    if symbol is None or symbol.get("status") != "TRADING":
        raise DemoAPIError("DEMO_BTCUSDT_NOT_TRADING")
    return symbol, {row["filterType"]: row for row in symbol["filters"]}


def _position(client: DemoClient) -> float:
    positions = client.request("GET", "/fapi/v2/positionRisk", {"symbol": SYMBOL}, signed=True)
    both = [row for row in positions if row.get("positionSide", "BOTH") == "BOTH"]
    if len(both) != 1:
        raise DemoAPIError("ONE_WAY_BTCUSDT_POSITION_NOT_AVAILABLE")
    return float(both[0]["positionAmt"])


def _open_orders(client: DemoClient) -> list[dict[str, Any]]:
    return client.request("GET", "/fapi/v1/openOrders", {"symbol": SYMBOL}, signed=True)


def _wait_order(client: DemoClient, order_id: int, terminal: set[str], timeout: float = 20) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    latest: dict[str, Any] = {}
    while time.monotonic() < deadline:
        latest = client.request(
            "GET",
            "/fapi/v1/order",
            {"symbol": SYMBOL, "orderId": order_id},
            signed=True,
        )
        if str(latest.get("status")) in terminal:
            return latest
        time.sleep(0.25)
    raise DemoAPIError(f"ORDER_STATE_TIMEOUT order={order_id} latest={latest.get('status')}")


def _account_snapshot(client: DemoClient, account_fingerprint: str) -> dict[str, Any]:
    account = client.request("GET", "/fapi/v2/account", signed=True)
    asset = next((row for row in account.get("assets", []) if row.get("asset") == "USDT"), {})
    return {
        "timestamp_utc": pd.Timestamp.now(tz="UTC").isoformat(),
        "account_fingerprint": account_fingerprint,
        "environment": "BINANCE_DEMO",
        "wallet_balance_usdt": asset.get("walletBalance"),
        "available_balance_usdt": asset.get("availableBalance"),
        "btc_position": _position(client),
        "btc_open_order_count": len(_open_orders(client)),
    }


def _minimum_quantity(filters: dict[str, Any], price: float, market: bool) -> float:
    lot = filters.get("MARKET_LOT_SIZE" if market else "LOT_SIZE", filters["LOT_SIZE"])
    step = float(lot["stepSize"])
    minimum = float(lot["minQty"])
    min_notional = float(filters.get("MIN_NOTIONAL", {}).get("notional", 0.0))
    quantity = max(minimum, min_notional / price if price else minimum)
    quantity = _round_up(quantity, step)
    if quantity > float(lot["maxQty"]):
        raise DemoAPIError("MINIMUM_SAFE_QUANTITY_EXCEEDS_MAXIMUM")
    return quantity


def _sanitized_user_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for event in events:
        event_type = event.get("e")
        order = event.get("o", {}) if event_type == "ORDER_TRADE_UPDATE" else {}
        account = event.get("a", {}) if event_type == "ACCOUNT_UPDATE" else {}
        rows.append({
            "event_type": event_type,
            "event_time": event.get("E"),
            "transaction_time": event.get("T"),
            "symbol": order.get("s"),
            "client_order_id": order.get("c"),
            "order_id": order.get("i"),
            "execution_type": order.get("x"),
            "order_status": order.get("X"),
            "last_fill_qty": order.get("l"),
            "last_fill_price": order.get("L"),
            "commission": order.get("n"),
            "commission_asset": order.get("N"),
            "account_update_reason": account.get("m"),
        })
    return rows


def _blocked(output: Path, reason: str, credential_presence: dict[str, bool]) -> int:
    output.mkdir(parents=True, exist_ok=True)
    common = [{"status": "BLOCKED", "reason": reason, "production_exchange_orders": 0}]
    _write_csv(output / "market_order_test.csv", common)
    _write_csv(output / "post_only_order_test.csv", common)
    _write_csv(output / "order_state_events.csv", [], ["event_type", "status"])
    _write_csv(output / "account_reconciliation.csv", common)
    validation = {
        "status": "BLOCKED",
        "reason": reason,
        "environment": "BINANCE_DEMO",
        "demo_http_endpoint": DEMO_HTTP,
        "demo_ws_endpoint": DEMO_WS,
        "credential_presence": credential_presence,
        "production_trading_endpoint_initialized": False,
        "production_exchange_orders": 0,
        "demo_exchange_orders": 0,
        "final_account_flat": None,
    }
    (output / "demo_environment_validation.json").write_text(json.dumps(validation, indent=2) + "\n")
    print(json.dumps(validation, indent=2))
    return 2


def run(output: Path, execute: bool) -> int:  # noqa: C901 sequential safety state machine
    api_key = os.environ.get("BINANCE_DEMO_API_KEY") or os.environ.get("BINANCE_DEMO_DIRECT_API_KEY")
    api_secret = os.environ.get("BINANCE_DEMO_API_SECRET") or os.environ.get("BINANCE_DEMO_DIRECT_API_SECRET")
    presence = {"api_key": bool(api_key), "api_secret": bool(api_secret)}
    if not api_key or not api_secret:
        return _blocked(output, "DEMO_CREDENTIALS_UNAVAILABLE", presence)
    if not execute:
        return _blocked(output, "EXPLICIT_DEMO_ORDER_FLAG_REQUIRED", presence)
    output.mkdir(parents=True, exist_ok=True)
    client = DemoClient(api_key, api_secret)
    fingerprint = hashlib.sha256(api_key.encode()).hexdigest()[:16]
    state_events: list[dict[str, Any]] = []
    snapshots: list[dict[str, Any]] = []
    market_rows: list[dict[str, Any]] = []
    maker_rows: list[dict[str, Any]] = []
    collector: UserDataCollector | None = None
    demo_orders = 0
    precheck_passed = False
    try:
        time_status = client.synchronize_time()
        instrument, filters = _instrument(client)
        dual = client.request("GET", "/fapi/v1/positionSide/dual", signed=True)
        if bool(dual.get("dualSidePosition")):
            raise DemoAPIError("HEDGE_MODE_UNSUPPORTED_FOR_CONTROLLED_SMOKE")
        before = _account_snapshot(client, fingerprint)
        snapshots.append({"stage": "PRECHECK", **before})
        if abs(float(before["btc_position"])) > 1e-12 or int(before["btc_open_order_count"]) != 0:
            raise DemoAPIError("ACCOUNT_NOT_FLAT_OR_HAS_OPEN_ORDERS")
        precheck_passed = True
        listen = client.request("POST", "/fapi/v1/listenKey", api_key_only=True)
        collector = UserDataCollector(str(listen["listenKey"]))
        collector.start()
        bbo = client.request("GET", "/fapi/v1/ticker/bookTicker", {"symbol": SYMBOL})
        ask, bid = float(bbo["askPrice"]), float(bbo["bidPrice"])
        market_qty = _minimum_quantity(filters, ask, market=True)
        market_client_id = f"NDEMOMKT{int(time.time())}"
        submitted_ms = int(time.time() * 1_000)
        opened = client.request("POST", "/fapi/v1/order", {
            "symbol": SYMBOL,
            "side": "BUY",
            "type": "MARKET",
            "quantity": f"{market_qty:.12f}".rstrip("0").rstrip("."),
            "newClientOrderId": market_client_id,
            "newOrderRespType": "RESULT",
        }, signed=True)
        demo_orders += 1
        open_final = _wait_order(client, int(opened["orderId"]), {"FILLED"})
        position_after_open = _position(client)
        if position_after_open <= 0:
            raise DemoAPIError("MARKET_OPEN_FILL_NOT_REFLECTED_IN_POSITION")
        flatten_qty = abs(position_after_open)
        flatten_client_id = f"NDEMOFLT{int(time.time())}"
        flattened = client.request("POST", "/fapi/v1/order", {
            "symbol": SYMBOL,
            "side": "SELL",
            "type": "MARKET",
            "quantity": f"{flatten_qty:.12f}".rstrip("0").rstrip("."),
            "reduceOnly": "true",
            "newClientOrderId": flatten_client_id,
            "newOrderRespType": "RESULT",
        }, signed=True)
        demo_orders += 1
        flatten_final = _wait_order(client, int(flattened["orderId"]), {"FILLED"})
        if abs(_position(client)) > 1e-12:
            raise DemoAPIError("MARKET_FLATTEN_RECONCILIATION_FAILED")
        trades = client.request("GET", "/fapi/v1/userTrades", {"symbol": SYMBOL, "startTime": submitted_ms - 1_000}, signed=True)
        relevant_trades = [row for row in trades if int(row.get("orderId", -1)) in {int(opened["orderId"]), int(flattened["orderId"])}]
        market_rows.append({
            "test": "DEMO_MARKET_ORDER_SMOKE",
            "status": "PASSED",
            "open_order_id": opened["orderId"],
            "open_client_order_id": market_client_id,
            "requested_quantity": market_qty,
            "filled_quantity": open_final.get("executedQty"),
            "average_fill_price": open_final.get("avgPrice"),
            "flatten_order_id": flattened["orderId"],
            "flatten_filled_quantity": flatten_final.get("executedQty"),
            "flatten_average_price": flatten_final.get("avgPrice"),
            "exchange_reported_commission": sum(float(row.get("commission", 0.0)) for row in relevant_trades),
            "commission_asset": ";".join(sorted({str(row.get("commissionAsset")) for row in relevant_trades})),
            "production_exchange_orders": 0,
        })
        bbo = client.request("GET", "/fapi/v1/ticker/bookTicker", {"symbol": SYMBOL})
        bid = float(bbo["bidPrice"])
        tick = float(filters["PRICE_FILTER"]["tickSize"])
        passive_price = _round_down(bid * 0.99, tick)
        maker_qty = _minimum_quantity(filters, passive_price, market=False)
        maker_client_id = f"NDEMOGTX{int(time.time())}"
        resting = client.request("POST", "/fapi/v1/order", {
            "symbol": SYMBOL,
            "side": "BUY",
            "type": "LIMIT",
            "timeInForce": "GTX",
            "quantity": f"{maker_qty:.12f}".rstrip("0").rstrip("."),
            "price": f"{passive_price:.12f}".rstrip("0").rstrip("."),
            "newClientOrderId": maker_client_id,
            "newOrderRespType": "ACK",
        }, signed=True)
        demo_orders += 1
        resting_state = _wait_order(client, int(resting["orderId"]), {"NEW", "PARTIALLY_FILLED", "FILLED"}, timeout=5)
        if resting_state.get("status") != "NEW" or float(resting_state.get("executedQty", 0.0)) != 0.0:
            raise DemoAPIError(f"POST_ONLY_DID_NOT_REST status={resting_state.get('status')}")
        canceled = client.request("DELETE", "/fapi/v1/order", {"symbol": SYMBOL, "orderId": resting["orderId"]}, signed=True)
        canceled_state = _wait_order(client, int(resting["orderId"]), {"CANCELED"})
        maker_rows.append({
            "test": "DEMO_POST_ONLY_ORDER_SMOKE",
            "status": "PASSED",
            "order_id": resting["orderId"],
            "client_order_id": maker_client_id,
            "side": "BUY",
            "requested_quantity": maker_qty,
            "price": passive_price,
            "time_in_force": "GTX",
            "initial_status": resting_state.get("status"),
            "executed_quantity_before_cancel": resting_state.get("executedQty"),
            "cancel_ack_status": canceled.get("status"),
            "final_status": canceled_state.get("status"),
            "production_exchange_orders": 0,
        })
        time.sleep(2)
        final_snapshot = _account_snapshot(client, fingerprint)
        snapshots.append({"stage": "FINAL", **final_snapshot})
        if abs(float(final_snapshot["btc_position"])) > 1e-12 or int(final_snapshot["btc_open_order_count"]) != 0:
            raise DemoAPIError("FINAL_ACCOUNT_NOT_FLAT_OR_HAS_OPEN_ORDERS")
        event_rows = _sanitized_user_events(collector.events)
        event_types = {row["event_type"] for row in event_rows}
        order_events = [row for row in event_rows if row["event_type"] == "ORDER_TRADE_UPDATE"]
        trade_updates = [row for row in order_events if row["execution_type"] == "TRADE"]
        if not order_events or not trade_updates or "ACCOUNT_UPDATE" not in event_types:
            raise DemoAPIError("USER_DATA_STREAM_RECONCILIATION_EVENTS_MISSING")
        state_events.extend(event_rows)
        _write_csv(output / "market_order_test.csv", market_rows)
        _write_csv(output / "post_only_order_test.csv", maker_rows)
        _write_csv(output / "order_state_events.csv", state_events)
        _write_csv(output / "account_reconciliation.csv", snapshots)
        validation = {
            "status": "PASSED",
            "environment": "BINANCE_DEMO",
            "demo_http_endpoint": DEMO_HTTP,
            "demo_ws_endpoint": DEMO_WS,
            "endpoint_identity_verified": True,
            "account_fingerprint": fingerprint,
            "server_time": time_status,
            "instrument": {
                "symbol": instrument["symbol"],
                "status": instrument["status"],
                "price_precision": instrument["pricePrecision"],
                "quantity_precision": instrument["quantityPrecision"],
            },
            "market_test": "PASSED",
            "post_only_test": "PASSED",
            "user_data_reconciliation": "PASSED",
            "final_account_flat": True,
            "final_open_orders": 0,
            "demo_exchange_orders": demo_orders,
            "production_trading_endpoint_initialized": False,
            "production_exchange_orders": 0,
            "funding": "DEMO_FUNDING_NOT_OBSERVED",
        }
        (output / "demo_environment_validation.json").write_text(json.dumps(validation, indent=2) + "\n")
        print(json.dumps(validation, indent=2))
        return 0
    except Exception as exc:
        reason = _safe_error(exc)
        cleanup_errors: list[str] = []
        if precheck_passed and demo_orders:
            try:
                client.request("DELETE", "/fapi/v1/allOpenOrders", {"symbol": SYMBOL}, signed=True)
            except Exception as cleanup_exc:
                cleanup_errors.append(f"cancel_all: {_safe_error(cleanup_exc)}")
            try:
                residual = _position(client)
                if abs(residual) > 1e-12:
                    cleanup = client.request("POST", "/fapi/v1/order", {
                        "symbol": SYMBOL,
                        "side": "SELL" if residual > 0 else "BUY",
                        "type": "MARKET",
                        "quantity": f"{abs(residual):.12f}".rstrip("0").rstrip("."),
                        "reduceOnly": "true",
                        "newClientOrderId": f"NDEMOEMG{int(time.time())}",
                        "newOrderRespType": "RESULT",
                    }, signed=True)
                    demo_orders += 1
                    _wait_order(client, int(cleanup["orderId"]), {"FILLED"})
            except Exception as cleanup_exc:
                cleanup_errors.append(f"flatten: {_safe_error(cleanup_exc)}")
        try:
            snapshots.append({"stage": "ERROR_FINAL", **_account_snapshot(client, fingerprint)})
        except Exception as snapshot_exc:
            snapshots.append({"stage": "ERROR_FINAL", "status": "SNAPSHOT_FAILED", "reason": _safe_error(snapshot_exc)})
        _write_csv(output / "market_order_test.csv", market_rows or [{"status": "BLOCKED", "reason": reason}])
        _write_csv(output / "post_only_order_test.csv", maker_rows or [{"status": "BLOCKED", "reason": reason}])
        _write_csv(output / "order_state_events.csv", _sanitized_user_events(collector.events if collector else []))
        _write_csv(output / "account_reconciliation.csv", snapshots)
        validation = {
            "status": "BLOCKED",
            "reason": reason,
            "environment": "BINANCE_DEMO",
            "demo_http_endpoint": DEMO_HTTP,
            "demo_ws_endpoint": DEMO_WS,
            "account_fingerprint": fingerprint,
            "demo_exchange_orders": demo_orders,
            "emergency_cleanup_errors": cleanup_errors,
            "production_trading_endpoint_initialized": False,
            "production_exchange_orders": 0,
            "final_account_flat": snapshots[-1].get("btc_position") == 0 if snapshots else None,
        }
        (output / "demo_environment_validation.json").write_text(json.dumps(validation, indent=2) + "\n")
        print(json.dumps(validation, indent=2))
        return 2
    finally:
        if collector is not None:
            collector.close()
            with contextlib.suppress(Exception):
                client.request("DELETE", "/fapi/v1/listenKey", api_key_only=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--execute-demo-orders", action="store_true")
    args = parser.parse_args()
    return run(args.output.resolve(), args.execute_demo_orders)


if __name__ == "__main__":
    raise SystemExit(main())
