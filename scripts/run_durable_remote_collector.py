#!/usr/bin/env python3
"""Durable, read-only Binance collector with a private WAL streaming endpoint.

This service owns its Binance public WebSocket independently of the Windows
paper process.  Every raw frame is appended locally before it is made visible
to downstream readers.  The TCP endpoint is intended for an encrypted private
network (Tailscale here) and is restricted to one configured client address.
It has no credentials and contains no account or order API.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import random
import signal
import time
from pathlib import Path

import websockets


def atomic_json(path: Path, value: dict) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


class DurableRawWal:
    def __init__(self, root: Path) -> None:
        self.root = root
        root.mkdir(parents=True, exist_ok=True)
        self.path = root / "events.jsonl"
        self.commit = root / "commit.json"
        self.sequence = 0
        self.committed_sequence = 0
        committed_offset = None
        if self.commit.exists():
            try:
                checkpoint = json.loads(self.commit.read_text())
                self.sequence = int(checkpoint["committed_sequence"])
                self.committed_sequence = self.sequence
                committed_offset = checkpoint.get("committed_offset")
            except Exception:
                self.sequence = 0
        if committed_offset is not None and self.path.exists():
            with self.path.open("r+b") as existing:
                existing.truncate(int(committed_offset))
        self.handle = self.path.open("a", encoding="utf-8", buffering=1024 * 1024)
        self.last_sync = time.monotonic()

    def append(self, raw: str, receive_ns: int) -> tuple[dict, bool]:
        self.sequence += 1
        row = {
            "collector_sequence": self.sequence,
            "source_receive_time_ns": receive_ns,
            "raw": raw,
        }
        self.handle.write(json.dumps(row, separators=(",", ":")) + "\n")
        committed = time.monotonic() - self.last_sync >= 0.5
        if committed:
            self.sync()
        return row, committed

    def sync(self) -> None:
        self.handle.flush()
        os.fsync(self.handle.fileno())
        committed_offset = self.handle.tell()
        self.committed_sequence = self.sequence
        atomic_json(self.commit, {
            "committed_sequence": self.committed_sequence,
            "committed_offset": committed_offset,
            "updated_at_ns": time.time_ns(),
        })
        self.last_sync = time.monotonic()

    def close(self) -> None:
        self.sync()
        self.handle.close()

    def is_committed(self, sequence: int) -> bool:
        return int(sequence) <= self.committed_sequence


async def main_async(args: argparse.Namespace) -> int:
    root = args.output.resolve()
    root.mkdir(parents=True, exist_ok=True)
    wal = DurableRawWal(root / "wal")
    stop = asyncio.Event()
    condition = asyncio.Condition()
    state = {
        "collector_id": args.collector_id,
        "status": "STARTING",
        "last_event_receive_ns": None,
        "last_trade_exchange_ms": None,
        "last_quote_exchange_ms": None,
        "reconnects": 0,
        "connected_since_ns": None,
        "committed_sequence": wal.sequence,
        "connected_clients": 0,
        "production_exchange_orders": 0,
    }

    def write_status() -> None:
        value = {**state, "updated_at_ns": time.time_ns()}
        last = value.get("last_event_receive_ns")
        value["last_event_age_seconds"] = None if last is None else (time.time_ns() - last) / 1e9
        atomic_json(root / "heartbeat.json", value)

    async def collect() -> None:
        rng = random.Random(args.collector_id)
        failure_streak = 0
        # Match the already validated USD-M public stream names used by the
        # Windows collector.  Futures `trade` supplies an exchange trade id.
        streams = "btcusdt@trade/btcusdt@bookTicker/btcusdt@markPrice@1s"
        url = f"wss://fstream.binance.com/stream?streams={streams}"
        while not stop.is_set():
            state["status"] = "CONNECTING"
            write_status()
            started = time.monotonic()
            try:
                async with websockets.connect(
                    url, open_timeout=15, ping_interval=20, ping_timeout=10,
                    close_timeout=5, max_queue=16384, proxy=None,
                ) as socket:
                    state["status"] = "HEALTHY"
                    state["connected_since_ns"] = time.time_ns()
                    write_status()
                    async for raw in socket:
                        receive_ns = time.time_ns()
                        row, committed = wal.append(raw, receive_ns)
                        state["committed_sequence"] = wal.committed_sequence
                        state["last_event_receive_ns"] = receive_ns
                        try:
                            payload = json.loads(raw).get("data", {})
                            if payload.get("e") in {"aggTrade", "trade"}:
                                state["last_trade_exchange_ms"] = payload.get("T", payload.get("E"))
                            elif payload.get("e") == "bookTicker":
                                state["last_quote_exchange_ms"] = payload.get("E", payload.get("T"))
                        except Exception:
                            pass
                        if committed:
                            # Readers are notified only after flush + fsync +
                            # checkpoint publication.  Appended is not committed.
                            async with condition:
                                condition.notify_all()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                state["status"] = "RECOVERING"
                state["last_error"] = f"{type(exc).__name__}: {exc}"
                state["reconnects"] += 1
                write_status()
            if stop.is_set():
                break
            lifetime = time.monotonic() - started
            failure_streak = 0 if lifetime >= 300 else failure_streak + 1
            delay = min(120.0, 2.0 ** max(0, failure_streak - 1)) + rng.random()
            try:
                await asyncio.wait_for(stop.wait(), delay)
            except asyncio.TimeoutError:
                pass

    async def heartbeat() -> None:
        while not stop.is_set():
            write_status()
            try:
                await asyncio.wait_for(stop.wait(), 5)
            except asyncio.TimeoutError:
                pass

    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        peer = writer.get_extra_info("peername")
        if not peer or peer[0] != args.allow_client:
            writer.close()
            await writer.wait_closed()
            return
        state["connected_clients"] += 1
        write_status()
        try:
            request = json.loads((await asyncio.wait_for(reader.readline(), 10)).decode())
            if request.get("live_only"):
                next_sequence = wal.committed_sequence + 1
            else:
                next_sequence = max(1, int(request.get("from_sequence", 1)))
            with wal.path.open(encoding="utf-8") as handle:
                while not stop.is_set():
                    position = handle.tell()
                    line = handle.readline()
                    if line:
                        try:
                            row = json.loads(line)
                        except json.JSONDecodeError:
                            await asyncio.sleep(0.05)
                            continue
                        sequence = int(row["collector_sequence"])
                        if sequence < next_sequence:
                            continue
                        if not wal.is_committed(sequence):
                            # The buffered writer can flush automatically before
                            # fsync.  Never expose such a line: rewind until its
                            # sequence is covered by the durable checkpoint.
                            handle.seek(position)
                            async with condition:
                                try:
                                    await asyncio.wait_for(condition.wait(), 1.0)
                                except asyncio.TimeoutError:
                                    pass
                            continue
                        row["forward_time_ns"] = time.time_ns()
                        writer.write((json.dumps(row, separators=(",", ":")) + "\n").encode())
                        await writer.drain()
                        next_sequence = sequence + 1
                        continue
                    async with condition:
                        try:
                            await asyncio.wait_for(condition.wait(), 1.0)
                        except asyncio.TimeoutError:
                            writer.write((json.dumps({
                                "heartbeat": True,
                                "committed_sequence": wal.committed_sequence,
                                "forward_time_ns": time.time_ns(),
                            }) + "\n").encode())
                            await writer.drain()
        except (ConnectionError, asyncio.IncompleteReadError, asyncio.TimeoutError, json.JSONDecodeError):
            pass
        finally:
            state["connected_clients"] = max(0, state["connected_clients"] - 1)
            write_status()
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:
            pass
    server = await asyncio.start_server(handle, args.bind, args.port)
    collector_task = asyncio.create_task(collect(), name="binance-public-collector")
    heartbeat_task = asyncio.create_task(heartbeat(), name="collector-heartbeat")
    async with server:
        await stop.wait()
    for task in (collector_task, heartbeat_task):
        task.cancel()
    await asyncio.gather(collector_task, heartbeat_task, return_exceptions=True)
    wal.close()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--collector-id", default="COLLECTOR_C_MAC_DIRECT")
    parser.add_argument("--bind", required=True)
    parser.add_argument("--port", type=int, default=7892)
    parser.add_argument("--allow-client", required=True)
    return asyncio.run(main_async(parser.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
