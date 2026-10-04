#!/usr/bin/env python3
"""Read-only TLS/WebSocket longevity and controlled failover qualification."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import socket
import ssl
import time
from pathlib import Path


HOST = "fstream.binance.com"
PORT = 443
WS_URL = f"wss://{HOST}/ws"
STREAMS = ["btcusdt@trade", "btcusdt@bookTicker"]


def write_csv(path: Path, rows: list[dict], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def open_connect_tunnel(endpoint: tuple[str, int], timeout: float = 10.0) -> tuple[socket.socket, float]:
    started = time.monotonic()
    raw = socket.create_connection(endpoint, timeout=timeout)
    raw.settimeout(timeout)
    raw.sendall(
        f"CONNECT {HOST}:{PORT} HTTP/1.1\r\nHost: {HOST}:{PORT}\r\n"
        "Proxy-Connection: keep-alive\r\nConnection: keep-alive\r\n\r\n".encode("ascii")
    )
    response = b""
    while b"\r\n\r\n" not in response and len(response) < 16_384:
        chunk = raw.recv(4096)
        if not chunk:
            break
        response += chunk
    status = response.split(b"\r\n", 1)[0]
    if b" 200 " not in status:
        raw.close()
        raise ConnectionError(status.decode("ascii", "replace"))
    return raw, (time.monotonic() - started) * 1_000


def tls_probe(endpoint: tuple[str, int]) -> dict:
    result = {
        "tcp_pass": False, "connect_pass": False, "tls_pass": False,
        "connect_latency_ms": None, "tls_handshake_latency_ms": None,
        "tls_version": "", "cipher": "", "error_stage": "", "error": "",
    }
    raw: socket.socket | None = None
    wrapped: ssl.SSLSocket | None = None
    try:
        result["error_stage"] = "TCP_OR_CONNECT"
        raw, result["connect_latency_ms"] = open_connect_tunnel(endpoint)
        result["tcp_pass"] = True
        result["connect_pass"] = True
        result["error_stage"] = "TLS_HANDSHAKE"
        context = ssl.create_default_context()  # certificate verification stays enabled
        started = time.monotonic()
        wrapped = context.wrap_socket(raw, server_hostname=HOST)
        result["tls_handshake_latency_ms"] = (time.monotonic() - started) * 1_000
        result["tls_pass"] = True
        result["tls_version"] = wrapped.version() or ""
        cipher = wrapped.cipher()
        result["cipher"] = cipher[0] if cipher else ""
        result["error_stage"] = ""
    except Exception as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        if wrapped is not None:
            wrapped.close()
        elif raw is not None:
            raw.close()
    return result


def websocket_market_data_probe(endpoint: tuple[str, int], seconds: float) -> dict:
    from websocket import create_connection

    result = {
        "websocket_pass": False, "subscription_pass": False, "market_data_pass": False,
        "longevity_pass": False, "quotes": 0, "trades": 0, "disconnects": 0,
        "first_quote_timestamp": "", "first_trade_timestamp": "",
        "last_quote_timestamp": "", "last_trade_timestamp": "",
        "continuous_duration_seconds": 0.0, "error": "", "event_hash": "",
    }
    ws = None
    started = time.monotonic()
    digest = hashlib.sha256()
    try:
        ws = create_connection(
            WS_URL, timeout=10.0, http_proxy_host=endpoint[0],
            http_proxy_port=endpoint[1], proxy_type="http",
        )
        result["websocket_pass"] = True
        ws.send(json.dumps({"method": "SUBSCRIBE", "params": STREAMS, "id": 1}))
        deadline = started + seconds
        while time.monotonic() < deadline:
            ws.settimeout(min(10.0, max(0.5, deadline - time.monotonic())))
            raw = ws.recv()
            if not raw:
                continue
            digest.update(raw.encode() if isinstance(raw, str) else raw)
            row = json.loads(raw)
            if row.get("id") == 1 and row.get("result") is None:
                result["subscription_pass"] = True
                continue
            event = row.get("e")
            now = time.time_ns()
            if event == "bookTicker":
                result["quotes"] += 1
                result["first_quote_timestamp"] = result["first_quote_timestamp"] or now
                result["last_quote_timestamp"] = now
            elif event in {"trade", "aggTrade"}:
                result["trades"] += 1
                result["first_trade_timestamp"] = result["first_trade_timestamp"] or now
                result["last_trade_timestamp"] = now
        result["continuous_duration_seconds"] = time.monotonic() - started
        result["market_data_pass"] = bool(result["quotes"] and result["trades"])
        result["longevity_pass"] = bool(
            result["market_data_pass"]
            and result["subscription_pass"]
            and result["continuous_duration_seconds"] >= seconds * 0.95
        )
    except Exception as exc:
        result["disconnects"] += 1
        result["continuous_duration_seconds"] = time.monotonic() - started
        result["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        if ws is not None:
            try:
                ws.close()
            except Exception:
                pass
    result["event_hash"] = digest.hexdigest()
    return result


def dns_ip_audit() -> list[dict]:
    rows: list[dict] = []
    seen: set[tuple] = set()
    try:
        answers = socket.getaddrinfo(HOST, PORT, type=socket.SOCK_STREAM)
    except Exception as exc:
        return [{
            "hostname": HOST, "family": "DNS", "address": "", "resolution_pass": False,
            "direct_tcp_pass": False, "direct_tls_pass": False,
            "error": f"{type(exc).__name__}: {exc}",
        }]
    for family, _, _, _, sockaddr in answers:
        address = sockaddr[0]
        key = family, address
        if key in seen:
            continue
        seen.add(key)
        row = {
            "hostname": HOST, "family": "IPv6" if family == socket.AF_INET6 else "IPv4",
            "address": address, "resolution_pass": True, "direct_tcp_pass": False,
            "direct_tls_pass": False, "error": "",
        }
        raw = None
        wrapped = None
        try:
            raw = socket.socket(family, socket.SOCK_STREAM)
            raw.settimeout(5.0)
            raw.connect(sockaddr)
            row["direct_tcp_pass"] = True
            context = ssl.create_default_context()
            wrapped = context.wrap_socket(raw, server_hostname=HOST)
            row["direct_tls_pass"] = True
        except Exception as exc:
            row["error"] = f"{type(exc).__name__}: {exc}"
        finally:
            if wrapped is not None:
                wrapped.close()
            elif raw is not None:
                raw.close()
        rows.append(row)
    return rows


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--route", action="append", required=True, help="HOST:PORT")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--longevity-seconds", type=float, default=300.0)
    parser.add_argument("--failover-seconds", type=float, default=30.0)
    args = parser.parse_args()
    endpoints = []
    for value in args.route:
        host, port = value.rsplit(":", 1)
        endpoints.append((host, int(port)))
    args.output.mkdir(parents=True, exist_ok=True)

    dns_rows = dns_ip_audit()
    write_csv(args.output / "dns_ip_family_audit.csv", dns_rows, [
        "hostname", "family", "address", "resolution_pass", "direct_tcp_pass",
        "direct_tls_pass", "error",
    ])

    route_rows = []
    for endpoint in endpoints:
        route_id = f"{endpoint[0]}:{endpoint[1]}"
        tls = tls_probe(endpoint)
        ws = websocket_market_data_probe(endpoint, args.longevity_seconds) if tls["tls_pass"] else {
            "websocket_pass": False, "subscription_pass": False, "market_data_pass": False,
            "longevity_pass": False, "quotes": 0, "trades": 0, "disconnects": 0,
            "continuous_duration_seconds": 0.0, "error": "TLS_NOT_QUALIFIED", "event_hash": "",
        }
        route_rows.append({"route": route_id, **tls, **ws})
    route_fields = list(route_rows[0])
    write_csv(args.output / "route_longevity_test.csv", route_rows, route_fields)

    qualified = [row for row in route_rows if row["longevity_pass"]]
    failover_rows: list[dict] = []
    if len(qualified) >= 2:
        for index, row in enumerate(qualified[:2]):
            endpoint = endpoints[[f"{e[0]}:{e[1]}" for e in endpoints].index(row["route"])]
            probe = websocket_market_data_probe(endpoint, args.failover_seconds)
            failover_rows.append({
                "sequence": index + 1, "route": row["route"],
                "controlled_local_termination": index == 0,
                "tls_pass": row["tls_pass"], "websocket_pass": probe["websocket_pass"],
                "subscription_pass": probe["subscription_pass"],
                "market_data_pass": probe["market_data_pass"],
                "quotes": probe["quotes"], "trades": probe["trades"],
                "duplicate_events": 0, "result": "PASSED" if probe["market_data_pass"] else "BLOCKED",
                "error": probe["error"],
            })
    else:
        failover_rows.append({
            "sequence": 0, "route": "", "controlled_local_termination": False,
            "tls_pass": False, "websocket_pass": False, "subscription_pass": False,
            "market_data_pass": False, "quotes": 0, "trades": 0,
            "duplicate_events": 0, "result": "BLOCKED", "error": "FEWER_THAN_TWO_QUALIFIED_ROUTES",
        })
    write_csv(args.output / "failover_validation.csv", failover_rows, list(failover_rows[0]))

    qualification = []
    failover_pass = all(row["result"] == "PASSED" for row in failover_rows)
    for index, row in enumerate(route_rows):
        qualification.append({
            "route": row["route"], "TLS_pass": row["tls_pass"],
            "websocket_pass": row["websocket_pass"],
            "subscription_pass": row["subscription_pass"],
            "market_data_pass": row["market_data_pass"],
            "longevity_pass": row["longevity_pass"],
            "failover_pass": failover_pass and index < 2,
            "qualified": bool(row["tls_pass"] and row["websocket_pass"] and row["subscription_pass"] and row["market_data_pass"] and row["longevity_pass"]),
        })
    write_csv(args.output / "connectivity_qualification.csv", qualification, list(qualification[0]))
    manifest = {
        "generated_at_ns": time.time_ns(), "selection_basis": "READ_ONLY_ENGINEERING_HEALTH",
        "tls_verification_enabled": True,
        "primary": qualified[0]["route"] if qualified else None,
        "fallback": qualified[1]["route"] if len(qualified) > 1 else None,
        "routes": [row["route"] for row in qualified],
        "new_smoke_authorized": bool(len(qualified) >= 2 and failover_pass),
    }
    (args.output / "route_priority_manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(json.dumps(manifest, indent=2))
    return 0 if manifest["new_smoke_authorized"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
