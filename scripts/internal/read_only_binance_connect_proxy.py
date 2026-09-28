#!/usr/bin/env python3
"""Tiny allow-listed HTTP CONNECT relay for public Binance market data only.

This relay accepts no ordinary HTTP methods and permits only the three public
market-data hosts below on TCP/443.  It exists for SSH-tunneled research hosts
whose direct outbound route is unavailable; it cannot reach account/order API
hosts outside the explicit allowlist.
"""

from __future__ import annotations

import argparse
import select
import socket
import threading


ALLOWED = {"fapi.binance.com", "fstream.binance.com", "data.binance.vision"}


def relay(client: socket.socket) -> None:
    upstream: socket.socket | None = None
    try:
        request = b""
        while b"\r\n\r\n" not in request and len(request) < 16_384:
            chunk = client.recv(4096)
            if not chunk: return
            request += chunk
        first = request.split(b"\r\n", 1)[0].decode("ascii", "replace")
        method, authority, _ = first.split(" ", 2)
        host, _, port_text = authority.rpartition(":")
        port = int(port_text)
        if method != "CONNECT" or host.lower() not in ALLOWED or port != 443:
            client.sendall(b"HTTP/1.1 403 Forbidden\r\nConnection: close\r\n\r\n")
            return
        upstream = socket.create_connection((host, port), timeout=20)
        client.sendall(b"HTTP/1.1 200 Connection Established\r\n\r\n")
        sockets = [client, upstream]
        while True:
            readable, _, exceptional = select.select(sockets, [], sockets, 60)
            if exceptional or not readable: return
            for source in readable:
                data = source.recv(65536)
                if not data: return
                (upstream if source is client else client).sendall(data)
    except Exception:
        try: client.sendall(b"HTTP/1.1 502 Bad Gateway\r\nConnection: close\r\n\r\n")
        except Exception: pass
    finally:
        client.close()
        if upstream is not None: upstream.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=18899)
    args = parser.parse_args()
    with socket.create_server((args.host, args.port), reuse_port=False) as server:
        while True:
            client, _ = server.accept()
            threading.Thread(target=relay, args=(client,), daemon=True).start()


if __name__ == "__main__":
    main()
