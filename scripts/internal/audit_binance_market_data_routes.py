#!/usr/bin/env python3
"""Probe authorized Binance public market-data routes without credentials/orders."""

from __future__ import annotations

import argparse
import base64
import csv
import hashlib
import os
import socket
import ssl
import struct
import time
from pathlib import Path


REMOTE = "fstream.binance.com"
PATH = "/stream?streams=btcusdt@aggTrade/btcusdt@bookTicker"


def open_route(proxy: str | None, timeout: float) -> tuple[socket.socket, list[dict]]:
    rows: list[dict] = []
    started = time.perf_counter()
    endpoint = REMOTE if proxy is None else proxy.split(":", 1)[0]
    port = 443 if proxy is None else int(proxy.split(":", 1)[1])
    try:
        socket.getaddrinfo(endpoint, port)
        rows.append({"stage": "DNS_RESOLUTION", "result": "SUCCESS", "latency_ms": (time.perf_counter() - started) * 1_000})
        started = time.perf_counter()
        raw = socket.create_connection((endpoint, port), timeout=timeout)
        rows.append({"stage": "TCP_CONNECT", "result": "SUCCESS", "latency_ms": (time.perf_counter() - started) * 1_000})
        if proxy is not None:
            started = time.perf_counter()
            raw.sendall(
                f"CONNECT {REMOTE}:443 HTTP/1.1\r\nHost: {REMOTE}:443\r\nConnection: keep-alive\r\n\r\n".encode()
            )
            response = b""
            while b"\r\n\r\n" not in response:
                response += raw.recv(4096)
            if b" 200 " not in response.split(b"\r\n", 1)[0]:
                raise ConnectionError(response.split(b"\r\n", 1)[0].decode(errors="replace"))
            rows.append({"stage": "PROXY_CONNECT", "result": "SUCCESS", "latency_ms": (time.perf_counter() - started) * 1_000})
        return raw, rows
    except Exception as exc:
        rows.append({
            "stage": "DIRECT_CONNECT" if proxy is None else "PROXY_CONNECT",
            "result": "FAILED", "latency_ms": (time.perf_counter() - started) * 1_000,
            "exception_type": type(exc).__name__, "exception_message": str(exc),
        })
        raise RouteProbeError(rows) from exc


class RouteProbeError(RuntimeError):
    def __init__(self, rows: list[dict]) -> None:
        super().__init__(rows[-1].get("exception_message", "route probe failed"))
        self.rows = rows


def receive_frame(sock: ssl.SSLSocket) -> int:
    header = sock.recv(2)
    if len(header) != 2:
        raise ConnectionError("WebSocket frame header ended early")
    length = header[1] & 0x7F
    if length == 126:
        length = struct.unpack("!H", sock.recv(2))[0]
    elif length == 127:
        length = struct.unpack("!Q", sock.recv(8))[0]
    remaining = length
    while remaining:
        chunk = sock.recv(min(65_536, remaining))
        if not chunk:
            raise ConnectionError("WebSocket payload ended early")
        remaining -= len(chunk)
    return header[0] & 0x0F


def probe(route: str, proxy: str | None, attempt: int, seconds: float, timeout: float) -> tuple[list[dict], dict]:
    connection_id = f"{route}-{attempt}-{time.time_ns()}"
    rows: list[dict] = []
    frame_count = 0
    connected_at = None
    try:
        raw, rows = open_route(proxy, timeout)
        started = time.perf_counter()
        context = ssl.create_default_context()  # certificate and hostname verification remain enabled
        tls = context.wrap_socket(raw, server_hostname=REMOTE)
        rows.append({"stage": "TLS_HANDSHAKE", "result": "SUCCESS", "latency_ms": (time.perf_counter() - started) * 1_000})
        started = time.perf_counter()
        key = base64.b64encode(os.urandom(16)).decode()
        tls.sendall(
            f"GET {PATH} HTTP/1.1\r\nHost: {REMOTE}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\nUser-Agent: nautilus-route-audit/1\r\n\r\n".encode()
        )
        response = b""
        while b"\r\n\r\n" not in response:
            response += tls.recv(4096)
        status = response.split(b"\r\n", 1)[0]
        if b" 101 " not in status:
            raise ConnectionError(f"upgrade failed: {status.decode(errors='replace')}")
        rows.append({"stage": "WEBSOCKET_UPGRADE", "result": "SUCCESS", "latency_ms": (time.perf_counter() - started) * 1_000})
        connected_at = time.perf_counter()
        deadline = connected_at + seconds
        tls.settimeout(min(2.0, seconds))
        while time.perf_counter() < deadline:
            try:
                opcode = receive_frame(tls)
                if opcode in {1, 2}:
                    frame_count += 1
                elif opcode == 8:
                    raise ConnectionError("server sent WebSocket close")
            except TimeoutError:
                continue
        rows.append({"stage": "WEBSOCKET_ACTIVE", "result": "SUCCESS", "latency_ms": (time.perf_counter() - connected_at) * 1_000})
        tls.close()
        status_value = "PASSED" if frame_count else "FAILED_NO_FRAMES"
    except RouteProbeError as exc:
        rows = exc.rows
        status_value = "FAILED"
    except Exception as exc:
        rows.append({
            "stage": "WEBSOCKET_ACTIVE" if connected_at else "TLS_OR_UPGRADE",
            "result": "FAILED", "latency_ms": "", "exception_type": type(exc).__name__,
            "exception_message": str(exc),
        })
        status_value = "FAILED"
    timestamp = time.time_ns()
    for row in rows:
        row.update({
            "timestamp": timestamp, "connection_id": connection_id, "route": route,
            "proxy_endpoint": proxy or "DIRECT", "remote_endpoint": REMOTE,
            "exception_type": row.get("exception_type", ""),
            "exception_message": row.get("exception_message", ""),
        })
    return rows, {
        "route": route, "proxy_endpoint": proxy or "DIRECT", "attempt": attempt,
        "status": status_value, "websocket_frames": frame_count,
        "connection_lifetime_seconds": 0.0 if connected_at is None else time.perf_counter() - connected_at,
        "tls_verification": "ENABLED", "production_orders": 0,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--attempts", type=int, default=3)
    parser.add_argument("--active-seconds", type=float, default=5.0)
    parser.add_argument("--timeout", type=float, default=8.0)
    parser.add_argument("--proxy", action="append", default=[])
    args = parser.parse_args()
    routes = [("DIRECT", None)] + [(f"PROXY_{value}", value) for value in args.proxy]
    timeline, summary = [], []
    for route, proxy in routes:
        for attempt in range(1, args.attempts + 1):
            rows, result = probe(route, proxy, attempt, args.active_seconds, args.timeout)
            timeline.extend(rows)
            summary.append(result)
    args.output.mkdir(parents=True, exist_ok=True)
    fields = [
        "timestamp", "connection_id", "stage", "route", "proxy_endpoint", "remote_endpoint",
        "result", "latency_ms", "exception_type", "exception_message",
    ]
    with (args.output / "connectivity_state_timeline.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(timeline)
    with (args.output / "route_connectivity_audit.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(summary[0]))
        writer.writeheader()
        writer.writerows(summary)
    digest = hashlib.sha256((args.output / "connectivity_state_timeline.csv").read_bytes()).hexdigest()
    print({"results": summary, "timeline_sha256": digest})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
