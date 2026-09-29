#!/usr/bin/env python3
"""Allow-listed Binance CONNECT proxy with redundant upstream proxies.

Designed for the Windows paper host: clients connect only to localhost, while
this process fails over between pre-authorized, always-on Tailscale HTTP proxy
nodes.  It accepts CONNECT to public Binance market-data hosts on port 443 only.
It has no credentials and cannot submit exchange orders.
"""

from __future__ import annotations

import argparse
import json
import select
import socket
import threading
import time
from pathlib import Path


ALLOWED = {"fapi.binance.com", "fstream.binance.com", "data.binance.vision"}
_log_lock = threading.Lock()


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


def relay(
    client: socket.socket,
    upstreams: list[tuple[str, int]],
    log_path: Path | None,
) -> None:
    upstream: socket.socket | None = None
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
            _write_log(log_path, {"event": "DENIED", "authority": authority})
            return
        errors = []
        for proxy in upstreams:
            try:
                upstream = _open_via_proxy(proxy, host, port)
                _write_log(log_path, {
                    "event": "UPSTREAM_CONNECTED", "target": authority,
                    "upstream": f"{proxy[0]}:{proxy[1]}",
                })
                break
            except Exception as exc:
                errors.append(f"{proxy[0]}:{proxy[1]}={exc!r}")
                _write_log(log_path, {
                    "event": "UPSTREAM_FAILED", "target": authority,
                    "upstream": f"{proxy[0]}:{proxy[1]}", "error": repr(exc),
                })
        if upstream is None:
            raise ConnectionError("; ".join(errors))
        client.sendall(b"HTTP/1.1 200 Connection Established\r\n\r\n")
        sockets = [client, upstream]
        while True:
            readable, _, exceptional = select.select(sockets, [], sockets, 60)
            if exceptional or not readable:
                return
            for source in readable:
                data = source.recv(65_536)
                if not data:
                    return
                (upstream if source is client else client).sendall(data)
    except Exception as exc:
        _write_log(log_path, {"event": "RELAY_FAILED", "error": repr(exc)})
        try:
            client.sendall(b"HTTP/1.1 502 Bad Gateway\r\nConnection: close\r\n\r\n")
        except Exception:
            pass
    finally:
        client.close()
        if upstream is not None:
            upstream.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=18898)
    parser.add_argument("--upstream", action="append", required=True, help="HOST:PORT")
    parser.add_argument("--log", type=Path)
    args = parser.parse_args()
    upstreams = []
    for value in args.upstream:
        host, port = value.rsplit(":", 1)
        upstreams.append((host, int(port)))
    with socket.create_server((args.host, args.port), reuse_port=False) as server:
        _write_log(args.log, {"event": "PROXY_STARTED", "upstreams": args.upstream})
        while True:
            client, _ = server.accept()
            threading.Thread(
                target=relay, args=(client, upstreams, args.log), daemon=True,
            ).start()


if __name__ == "__main__":
    main()
