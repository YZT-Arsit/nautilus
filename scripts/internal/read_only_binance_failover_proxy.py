#!/usr/bin/env python3
"""
Allow-listed Binance CONNECT proxy with redundant upstream proxies.

Designed for the Windows paper host: clients connect only to localhost, while
this process fails over between pre-authorized, always-on Tailscale HTTP proxy
nodes.  It accepts CONNECT to public Binance market-data hosts on port 443 only.
It has no credentials and cannot submit exchange orders.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import select
import socket
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path


ALLOWED = {"fapi.binance.com", "fstream.binance.com", "data.binance.vision"}
_log_lock = threading.Lock()


@dataclass
class RouteState:
    endpoint: tuple[str, int]
    consecutive_failures: int = 0
    cooldown_until: float = 0.0
    state: str = "CLOSED"
    half_open_probe_in_flight: bool = False
    last_failure_at: float | None = None
    half_open_probe_at: float | None = None
    successful_probe_count: int = 0


class RouteHealthManager:
    """
    Thread-safe route rotation with a small circuit breaker.

    A successful HTTP CONNECT is not enough to establish route health because
    the upstream proxy cannot inspect the tunneled TLS handshake.  Short-lived
    tunnels and relay resets therefore penalize the selected route too.
    """

    def __init__(
        self,
        endpoints: list[tuple[str, int]],
        *,
        cooldown_seconds: float = 60.0,
        max_cooldown_seconds: float = 600.0,
        clock=time.monotonic,
    ) -> None:
        if not endpoints:
            raise ValueError("at least one upstream route is required")
        self._routes = [RouteState(endpoint=value) for value in endpoints]
        self._cooldown_seconds = cooldown_seconds
        self._max_cooldown_seconds = max_cooldown_seconds
        self._clock = clock
        self._cursor = 0
        self._lock = threading.Lock()

    def acquire(self) -> tuple[tuple[str, int] | None, float, bool]:
        """Reserve one eligible route.

        An expired OPEN circuit may have only one HALF_OPEN probe in flight.
        Callers never sleep inside the proxy; when every route is unavailable
        the caller gets a wait hint and fails fast.  This prevents timed-out
        client connections from accumulating and waking as a probe stampede.
        """
        with self._lock:
            now = self._clock()
            for offset in range(len(self._routes)):
                index = (self._cursor + offset) % len(self._routes)
                route = self._routes[index]
                if route.state == "CLOSED":
                    self._cursor = (index + 1) % len(self._routes)
                    return route.endpoint, 0.0, False
                if (
                    route.state == "OPEN"
                    and route.cooldown_until <= now
                    and not route.half_open_probe_in_flight
                ):
                    route.state = "HALF_OPEN"
                    route.half_open_probe_in_flight = True
                    route.half_open_probe_at = now
                    self._cursor = (index + 1) % len(self._routes)
                    return route.endpoint, 0.0, True
            wait = max(0.0, min(route.cooldown_until for route in self._routes) - now)
            return None, wait, False

    def record_failure(self, endpoint: tuple[str, int]) -> float:
        with self._lock:
            now = self._clock()
            index = next(i for i, route in enumerate(self._routes) if route.endpoint == endpoint)
            route = self._routes[index]
            route.consecutive_failures += 1
            delay = min(
                self._cooldown_seconds * (2 ** (route.consecutive_failures - 1)),
                self._max_cooldown_seconds,
            )
            route.cooldown_until = now + delay
            route.state = "OPEN"
            route.half_open_probe_in_flight = False
            route.last_failure_at = now
            self._cursor = (index + 1) % len(self._routes)
            return delay

    def record_success(self, endpoint: tuple[str, int]) -> bool:
        """Close a half-open circuit after a stable relay; return recovery flag."""
        with self._lock:
            index = next(i for i, route in enumerate(self._routes) if route.endpoint == endpoint)
            route = self._routes[index]
            recovered = route.consecutive_failures > 0 or route.state == "HALF_OPEN"
            if route.state == "HALF_OPEN":
                route.successful_probe_count += 1
            route.consecutive_failures = 0
            route.cooldown_until = 0.0
            route.state = "CLOSED"
            route.half_open_probe_in_flight = False
            self._cursor = index
            return recovered

    def snapshot(self) -> list[dict]:
        with self._lock:
            return [
                {
                    "route_id": f"{route.endpoint[0]}:{route.endpoint[1]}",
                    "state": route.state,
                    "failure_count": route.consecutive_failures,
                    "last_failure_timestamp_monotonic": route.last_failure_at,
                    "open_until_monotonic": route.cooldown_until,
                    "half_open_probe_timestamp_monotonic": route.half_open_probe_at,
                    "successful_probe_count": route.successful_probe_count,
                    "half_open_probe_in_flight": route.half_open_probe_in_flight,
                }
                for route in self._routes
            ]


def _write_log(path: Path | None, row: dict) -> None:
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with _log_lock, path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({"time_ns": time.time_ns(), **row}, sort_keys=True) + "\n")


def _open_via_proxy(proxy: tuple[str, int], host: str, port: int) -> socket.socket:
    upstream = socket.create_connection(proxy, timeout=10)
    upstream.settimeout(10)
    upstream.sendall(
        f"CONNECT {host}:{port} HTTP/1.1\r\nHost: {host}:{port}\r\n"
        "Proxy-Connection: keep-alive\r\nConnection: keep-alive\r\n\r\n".encode("ascii")
    )
    response = b""
    while b"\r\n\r\n" not in response and len(response) < 16_384:
        chunk = upstream.recv(4096)
        if not chunk:
            break
        response += chunk
    status = response.split(b"\r\n", 1)[0]
    if b" 200 " not in status:
        upstream.close()
        raise ConnectionError(f"upstream CONNECT failed: {status.decode('ascii', 'replace')}")
    upstream.settimeout(None)
    return upstream


def relay(  # noqa: C901
    client: socket.socket,
    route_manager: RouteHealthManager,
    log_path: Path | None,
    *,
    short_lived_seconds: float = 30.0,
) -> None:
    connection_id = uuid.uuid4().hex
    upstream: socket.socket | None = None
    selected_route: tuple[str, int] | None = None
    target = ""
    relay_started = 0.0
    bytes_client_to_upstream = 0
    bytes_upstream_to_client = 0
    end_reason = "request_incomplete"
    try:
        request = b""
        while b"\r\n\r\n" not in request and len(request) < 16_384:
            chunk = client.recv(4096)
            if not chunk:
                return
            request += chunk
        first = request.split(b"\r\n", 1)[0].decode("ascii", "replace")
        method, authority, _ = first.split(" ", 2)
        host, _, port_text = authority.rpartition(":")
        port = int(port_text)
        if method != "CONNECT" or host.lower() not in ALLOWED or port != 443:
            client.sendall(b"HTTP/1.1 403 Forbidden\r\nConnection: close\r\n\r\n")
            _write_log(log_path, {
                "event": "DENIED", "connection_id": connection_id, "authority": authority,
            })
            return
        target = authority
        errors = []
        attempted: set[tuple[str, int]] = set()
        while len(attempted) < len(route_manager.snapshot()):
            proxy, wait_seconds, half_open = route_manager.acquire()
            if proxy is None:
                if wait_seconds > 0:
                    _write_log(log_path, {
                        "event": "DEGRADED_WAIT", "connection_id": connection_id,
                        "target": authority, "wait_seconds": wait_seconds,
                        "route_states": route_manager.snapshot(),
                    })
                break
            if proxy in attempted:
                break
            attempted.add(proxy)
            attempt_started = time.monotonic()
            _write_log(log_path, {
                "event": "CONNECT_ATTEMPT", "connection_id": connection_id,
                "target": authority, "upstream": f"{proxy[0]}:{proxy[1]}",
                "half_open": half_open, "route_states": route_manager.snapshot(),
            })
            try:
                upstream = _open_via_proxy(proxy, host, port)
                selected_route = proxy
                _write_log(log_path, {
                    "event": "UPSTREAM_CONNECTED", "connection_id": connection_id,
                    "target": authority,
                    "upstream": f"{proxy[0]}:{proxy[1]}",
                    "connect_latency_ms": (time.monotonic() - attempt_started) * 1_000,
                    "half_open": half_open,
                })
                break
            except Exception as exc:
                cooldown = route_manager.record_failure(proxy)
                errors.append(f"{proxy[0]}:{proxy[1]}={exc!r}")
                _write_log(log_path, {
                    "event": "UPSTREAM_FAILED", "connection_id": connection_id,
                    "target": authority,
                    "upstream": f"{proxy[0]}:{proxy[1]}", "error": repr(exc),
                    "connect_latency_ms": (time.monotonic() - attempt_started) * 1_000,
                    "cooldown_seconds": cooldown, "route_states": route_manager.snapshot(),
                })
        if upstream is None:
            if errors:
                raise ConnectionError("; ".join(errors))
            client.sendall(b"HTTP/1.1 503 Service Unavailable\r\nRetry-After: 30\r\nConnection: close\r\n\r\n")
            end_reason = "degraded_wait"
            return
        client.sendall(b"HTTP/1.1 200 Connection Established\r\n\r\n")
        relay_started = time.monotonic()
        end_reason = "unknown"
        sockets = [client, upstream]
        while True:
            readable, _, exceptional = select.select(sockets, [], sockets, 60)
            if exceptional:
                end_reason = "socket_exception"
                return
            if not readable:
                end_reason = "idle_timeout"
                return
            for source in readable:
                data = source.recv(65_536)
                if not data:
                    end_reason = "client_eof" if source is client else "upstream_eof"
                    return
                if source is client:
                    bytes_client_to_upstream += len(data)
                    upstream.sendall(data)
                else:
                    bytes_upstream_to_client += len(data)
                    client.sendall(data)
    except Exception as exc:
        end_reason = "relay_exception"
        _write_log(log_path, {
            "event": "RELAY_FAILED", "connection_id": connection_id,
            "target": target, "upstream": (
                f"{selected_route[0]}:{selected_route[1]}" if selected_route else None
            ), "error": repr(exc),
        })
        with contextlib.suppress(Exception):
            client.sendall(b"HTTP/1.1 502 Bad Gateway\r\nConnection: close\r\n\r\n")
    finally:
        if selected_route is not None and relay_started:
            lifetime = max(0.0, time.monotonic() - relay_started)
            # A short WebSocket tunnel means CONNECT succeeded but TLS or the
            # upgraded stream died.  REST tunnels are expected to be brief.
            unhealthy = (
                target.lower().startswith("fstream.binance.com:")
                and end_reason in {"relay_exception", "socket_exception", "upstream_eof"}
            )
            if unhealthy:
                cooldown = route_manager.record_failure(selected_route)
                _write_log(log_path, {
                    "event": "ROUTE_PENALIZED", "connection_id": connection_id,
                    "target": target, "upstream": f"{selected_route[0]}:{selected_route[1]}",
                    "reason": end_reason, "cooldown_seconds": cooldown,
                    "route_states": route_manager.snapshot(),
                })
            elif lifetime >= short_lived_seconds:
                recovered = route_manager.record_success(selected_route)
                if recovered:
                    _write_log(log_path, {
                        "event": "TRANSPORT_ROUTE_RECOVERED", "connection_id": connection_id,
                        "target": target,
                        "upstream": f"{selected_route[0]}:{selected_route[1]}",
                        "route_states": route_manager.snapshot(),
                    })
            _write_log(log_path, {
                "event": "RELAY_ENDED", "connection_id": connection_id,
                "target": target, "upstream": f"{selected_route[0]}:{selected_route[1]}",
                "relay_lifetime_seconds": lifetime,
                "bytes_client_to_upstream": bytes_client_to_upstream,
                "bytes_upstream_to_client": bytes_upstream_to_client,
                "end_reason": end_reason, "route_unhealthy": unhealthy,
            })
        client.close()
        if upstream is not None:
            upstream.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=18898)
    parser.add_argument("--upstream", action="append", required=True, help="HOST:PORT")
    parser.add_argument("--log", type=Path)
    parser.add_argument("--route-cooldown-seconds", type=float, default=60.0)
    parser.add_argument("--route-max-cooldown-seconds", type=float, default=600.0)
    parser.add_argument("--short-lived-seconds", type=float, default=30.0)
    args = parser.parse_args()
    upstreams = []
    for value in args.upstream:
        host, port = value.rsplit(":", 1)
        upstreams.append((host, int(port)))
    route_manager = RouteHealthManager(
        upstreams,
        cooldown_seconds=args.route_cooldown_seconds,
        max_cooldown_seconds=args.route_max_cooldown_seconds,
    )
    with socket.create_server((args.host, args.port), reuse_port=False) as server:
        _write_log(args.log, {"event": "PROXY_STARTED", "upstreams": args.upstream})
        while True:
            client, _ = server.accept()
            threading.Thread(
                target=relay, args=(client, route_manager, args.log),
                kwargs={"short_lived_seconds": args.short_lived_seconds}, daemon=True,
            ).start()


if __name__ == "__main__":
    main()
