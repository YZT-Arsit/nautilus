from __future__ import annotations

import importlib.util
import json
import socket
import sys
import threading
from pathlib import Path


MODULE_PATH = (
    Path(__file__).resolve().parents[1]
    / "scripts"
    / "internal"
    / "read_only_binance_failover_proxy.py"
)
SPEC = importlib.util.spec_from_file_location("read_only_binance_failover_proxy", MODULE_PATH)
assert SPEC is not None
assert SPEC.loader is not None
proxy = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = proxy
SPEC.loader.exec_module(proxy)


class FakeClock:
    def __init__(self) -> None:
        self.value = 100.0

    def __call__(self) -> float:
        return self.value


def test_route_failure_rotates_and_half_open_recovers() -> None:
    clock = FakeClock()
    first = ("100.64.0.5", 7890)
    second = ("100.64.0.6", 7890)
    manager = proxy.RouteHealthManager(
        [first, second], cooldown_seconds=10.0, max_cooldown_seconds=60.0, clock=clock,
    )

    route, wait, half_open = manager.acquire()
    assert route == first
    assert wait == 0.0
    assert half_open is False

    assert manager.record_failure(first) == 10.0
    route, wait, half_open = manager.acquire()
    assert route == second
    assert wait == 0.0
    assert half_open is False

    clock.value += 11.0
    # The closed second route remains available; fail it to exercise the
    # expired OPEN route's single HALF_OPEN reservation.
    manager.record_failure(second)
    route, wait, half_open = manager.acquire()
    assert route == first
    assert wait == 0.0
    assert half_open is True
    assert manager.record_success(first) is True

    snapshot = {row["route_id"]: row for row in manager.snapshot()}
    assert snapshot["100.64.0.5:7890"]["state"] == "CLOSED"
    assert snapshot["100.64.0.5:7890"]["half_open_probe_in_flight"] is False


def test_route_cooldown_is_exponential_and_bounded() -> None:
    clock = FakeClock()
    route = ("100.64.0.5", 7890)
    manager = proxy.RouteHealthManager(
        [route], cooldown_seconds=5.0, max_cooldown_seconds=12.0, clock=clock,
    )

    assert manager.record_failure(route) == 5.0
    clock.value += 5.0
    assert manager.record_failure(route) == 10.0
    clock.value += 10.0
    assert manager.record_failure(route) == 12.0


def test_client_closed_websocket_does_not_penalize_selected_route(monkeypatch, tmp_path) -> None:
    first = ("100.64.0.5", 7890)
    second = ("100.64.0.6", 7890)
    manager = proxy.RouteHealthManager(
        [first, second], cooldown_seconds=60.0, max_cooldown_seconds=60.0,
    )
    client_side, caller_side = socket.socketpair()
    upstream_side, remote_side = socket.socketpair()
    monkeypatch.setattr(proxy, "_open_via_proxy", lambda route, host, port: upstream_side)
    log_path = tmp_path / "proxy.jsonl"

    thread = threading.Thread(
        target=proxy.relay,
        args=(client_side, manager, log_path),
        kwargs={"short_lived_seconds": 30.0},
    )
    thread.start()
    caller_side.sendall(
        b"CONNECT fstream.binance.com:443 HTTP/1.1\r\n"
        b"Host: fstream.binance.com:443\r\n\r\n"
    )
    response = caller_side.recv(4096)
    assert b"200 Connection Established" in response
    caller_side.close()
    thread.join(timeout=5.0)
    remote_side.close()
    assert not thread.is_alive()

    route, _, _ = manager.acquire()
    assert route == second
    rows = [json.loads(line) for line in log_path.read_text().splitlines()]
    connected = next(row for row in rows if row["event"] == "UPSTREAM_CONNECTED")
    ended = next(row for row in rows if row["event"] == "RELAY_ENDED")
    assert connected["connection_id"] == ended["connection_id"]
    assert connected["upstream"] == "100.64.0.5:7890"
    assert connected["connect_latency_ms"] >= 0.0
    assert ended["relay_lifetime_seconds"] < 30.0
    assert ended["bytes_client_to_upstream"] == 0
    assert ended["bytes_upstream_to_client"] == 0
    assert ended["end_reason"] == "client_eof"
    assert ended["route_unhealthy"] is False
    assert not any(row["event"] == "ROUTE_PENALIZED" for row in rows)


def test_short_rest_tunnel_does_not_penalize_route(monkeypatch, tmp_path) -> None:
    first = ("100.64.0.5", 7890)
    manager = proxy.RouteHealthManager([first], cooldown_seconds=60.0)
    client_side, caller_side = socket.socketpair()
    upstream_side, remote_side = socket.socketpair()
    monkeypatch.setattr(proxy, "_open_via_proxy", lambda route, host, port: upstream_side)
    log_path = tmp_path / "proxy.jsonl"

    thread = threading.Thread(
        target=proxy.relay,
        args=(client_side, manager, log_path),
        kwargs={"short_lived_seconds": 30.0},
    )
    thread.start()
    caller_side.sendall(
        b"CONNECT fapi.binance.com:443 HTTP/1.1\r\n"
        b"Host: fapi.binance.com:443\r\n\r\n"
    )
    assert b"200 Connection Established" in caller_side.recv(4096)
    caller_side.close()
    thread.join(timeout=5.0)
    remote_side.close()

    route, _, _ = manager.acquire()
    assert route == first
    rows = [json.loads(line) for line in log_path.read_text().splitlines()]
    ended = next(row for row in rows if row["event"] == "RELAY_ENDED")
    assert ended["route_unhealthy"] is False
    assert not any(row["event"] == "ROUTE_PENALIZED" for row in rows)


def test_only_one_half_open_probe_is_reserved() -> None:
    clock = FakeClock()
    route = ("100.64.0.5", 7890)
    manager = proxy.RouteHealthManager([route], cooldown_seconds=10.0, clock=clock)
    manager.record_failure(route)
    clock.value += 11.0

    selected, wait, half_open = manager.acquire()
    assert selected == route
    assert wait == 0.0
    assert half_open is True

    selected, wait, half_open = manager.acquire()
    assert selected is None
    assert half_open is False


def test_short_half_open_client_close_releases_probe(monkeypatch, tmp_path) -> None:
    clock = FakeClock()
    route = ("100.64.0.5", 7890)
    manager = proxy.RouteHealthManager([route], cooldown_seconds=10.0, clock=clock)
    manager.record_failure(route)
    clock.value += 11.0

    client_side, caller_side = socket.socketpair()
    upstream_side, remote_side = socket.socketpair()
    monkeypatch.setattr(proxy, "_open_via_proxy", lambda selected, host, port: upstream_side)
    log_path = tmp_path / "proxy.jsonl"
    thread = threading.Thread(
        target=proxy.relay,
        args=(client_side, manager, log_path),
        kwargs={"short_lived_seconds": 30.0},
    )
    thread.start()
    caller_side.sendall(
        b"CONNECT fstream.binance.com:443 HTTP/1.1\r\n"
        b"Host: fstream.binance.com:443\r\n\r\n"
    )
    assert b"200 Connection Established" in caller_side.recv(4096)
    caller_side.close()
    thread.join(timeout=5.0)
    remote_side.close()

    snapshot = manager.snapshot()[0]
    assert snapshot["state"] == "OPEN"
    assert snapshot["half_open_probe_in_flight"] is False
    rows = [json.loads(line) for line in log_path.read_text().splitlines()]
    assert any(row["event"] == "HALF_OPEN_PROBE_FAILED" for row in rows)


def test_all_routes_cooling_fails_fast_without_sleep(monkeypatch, tmp_path) -> None:
    route = ("100.64.0.5", 7890)
    manager = proxy.RouteHealthManager([route], cooldown_seconds=60.0)
    manager.record_failure(route)
    client_side, caller_side = socket.socketpair()
    log_path = tmp_path / "proxy.jsonl"
    thread = threading.Thread(target=proxy.relay, args=(client_side, manager, log_path))
    thread.start()
    caller_side.sendall(
        b"CONNECT fstream.binance.com:443 HTTP/1.1\r\n"
        b"Host: fstream.binance.com:443\r\n\r\n"
    )
    response = caller_side.recv(4096)
    thread.join(timeout=2.0)
    caller_side.close()
    assert b"503 Service Unavailable" in response
    assert not thread.is_alive()
    rows = [json.loads(line) for line in log_path.read_text().splitlines()]
    assert any(row["event"] == "DEGRADED_WAIT" for row in rows)
