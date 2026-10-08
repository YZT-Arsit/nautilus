#!/usr/bin/env python3
"""Restricted HTTP CONNECT relay for Binance public TLS/WebSocket traffic."""

from __future__ import annotations

import argparse
import asyncio
import json
import time


async def copy_stream(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        while data := await reader.read(256 * 1024):
            writer.write(data); await writer.drain()
    except (ConnectionError, asyncio.CancelledError):
        pass
    finally:
        writer.close()


async def main_async(args) -> None:
    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        peer = writer.get_extra_info("peername")
        allowed = peer and peer[0] == args.allow_client
        try:
            header = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 10)
            first = header.split(b"\r\n", 1)[0].decode("ascii", "replace")
            method, authority, _ = first.split(" ", 2)
            host, port = authority.rsplit(":", 1)
            if not allowed or method != "CONNECT" or host != "fstream.binance.com" or int(port) != 443:
                writer.write(b"HTTP/1.1 403 Forbidden\r\nConnection: close\r\n\r\n"); await writer.drain(); return
            upstream_reader, upstream_writer = await asyncio.open_connection(host, int(port))
            writer.write(b"HTTP/1.1 200 Connection established\r\n\r\n"); await writer.drain()
            print(json.dumps({"ts": time.time_ns(), "peer": peer[0], "target": authority, "status": "OPEN"}), flush=True)
            await asyncio.gather(copy_stream(reader, upstream_writer), copy_stream(upstream_reader, writer))
        except Exception as exc:
            print(json.dumps({"ts": time.time_ns(), "peer": peer, "status": "ERROR",
                              "error": f"{type(exc).__name__}: {exc}"}), flush=True)
        finally:
            writer.close(); await writer.wait_closed()

    server = await asyncio.start_server(handle, args.bind, args.port)
    async with server:
        await server.serve_forever()


def main() -> None:
    parser = argparse.ArgumentParser(); parser.add_argument("--bind", required=True)
    parser.add_argument("--port", type=int, required=True); parser.add_argument("--allow-client", required=True)
    asyncio.run(main_async(parser.parse_args()))


if __name__ == "__main__":
    main()
