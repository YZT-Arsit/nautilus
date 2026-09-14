#!/usr/bin/env python3
"""Finish the authorized public five-year maker-data acquisition safely."""

from __future__ import annotations

import argparse
import subprocess
import time
from pathlib import Path


REMOTE = "laok@100.64.0.17"
REMOTE_REPORT = r"D:\nautilus\outputs\baseline_evaluation\five_year_maker_data_acquisition"
REMOTE_DATA = r"D:\nautilus\historical_data\five_year_maker"
REMOTE_TEMP = r"D:\nautilus\outputs\tmp_five_year_maker_daily"
REMOTE_PYTHON = r"D:\nautilus\.venv\Scripts\python.exe"
REMOTE_ACQUIRE = r"D:\nautilus\scripts\internal\acquire_five_year_public_daily_sharded.py"
REMOTE_FINALIZE = r"D:\nautilus\scripts\internal\finalize_five_year_maker_data_acquisition.py"


def ssh_args(socket: Path) -> list[str]:
    return [
        "ssh", "-S", str(socket), "-o", "ControlMaster=no", "-o", "BatchMode=yes",
        REMOTE,
    ]


def run_ssh(socket: Path, command: str, *, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [*ssh_args(socket), command], text=True, stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT, check=check,
    )


def channel_ready(socket: Path) -> bool:
    if not socket.exists():
        return False
    result = run_ssh(socket, "echo READY", check=False)
    return result.returncode == 0 and "READY" in result.stdout


def lock_count(socket: Path) -> int:
    command = (
        "powershell -NoProfile -Command \""
        f"(Get-ChildItem '{REMOTE_REPORT}' -Filter '.daily_worker_*' "
        "-ErrorAction SilentlyContinue).Count\""
    )
    result = run_ssh(socket, command)
    return int(result.stdout.strip().splitlines()[-1])


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--socket", type=Path, default=Path("/tmp/nautilus-maker-ssh.sock"))
    parser.add_argument(
        "--local-report",
        type=Path,
        default=Path("outputs/baseline_evaluation/five_year_maker_data_acquisition"),
    )
    parser.add_argument("--poll-seconds", type=int, default=60)
    args = parser.parse_args()

    while not channel_ready(args.socket):
        print("waiting_for_ssh_channel", flush=True)
        time.sleep(args.poll_seconds)
    while lock_count(args.socket):
        print("waiting_for_active_workers", flush=True)
        time.sleep(args.poll_seconds)

    # Re-run all shard identities sequentially. Each shard verifies and skips
    # completed outputs, so this fills only partitions missed by interrupted workers.
    for worker in range(8):
        command = (
            f"{REMOTE_PYTHON} {REMOTE_ACQUIRE} --symbol BTCUSDT "
            f"--worker-index {worker} --worker-count 8 --output-root {REMOTE_DATA} "
            f"--report-root {REMOTE_REPORT} --temp-root {REMOTE_TEMP}"
        )
        result = run_ssh(args.socket, command, check=False)
        print(result.stdout, end="", flush=True)
        if result.returncode:
            raise SystemExit(f"worker {worker} failed with exit code {result.returncode}")

    finalize = (
        f"{REMOTE_PYTHON} {REMOTE_FINALIZE} --report-root {REMOTE_REPORT} "
        f"--data-root {REMOTE_DATA} --temp-root {REMOTE_TEMP}"
    )
    result = run_ssh(args.socket, finalize)
    print(result.stdout, end="", flush=True)

    args.local_report.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "scp", "-r", "-o", f"ControlPath={args.socket}", "-o", "ControlMaster=no",
            "-o", "BatchMode=yes", f"{REMOTE}:{REMOTE_REPORT.replace(chr(92), '/')}",
            str(args.local_report.parent),
        ],
        check=True,
    )
    print("PUBLIC_ACQUISITION_FINALIZED_AND_REPORTS_MIRRORED", flush=True)


if __name__ == "__main__":
    main()
