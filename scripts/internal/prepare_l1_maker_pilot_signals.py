#!/usr/bin/env python3
"""Build frozen March-2024 pilot targets from official bars; no maker execution."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import urllib.request
import zipfile
from dataclasses import replace
from pathlib import Path

import numpy as np
import pandas as pd
import yaml


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from data_engine.events import BarEvent  # noqa: E402
from scripts.internal.run_all_strategy_timeframe_lag import (  # noqa: E402
    build_strategy_clock,
    run_decision_lifecycle,
)


SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT")
START = pd.Timestamp("2024-03-01", tz="UTC")
END = pd.Timestamp("2024-03-31", tz="UTC")
WARMUP_START = pd.Timestamp("2024-01-01", tz="UTC")
MONTHS = ("2024-01", "2024-02", "2024-03")
PILOT_PLAN = Path(
    "outputs/baseline_evaluation/maker_execution_research/data_pilot/maker_pilot_scope.csv"
)
OUTPUT = Path("outputs/baseline_evaluation/maker_execution_research/l1_pilot")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(4 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def fetch_checked(url: str, temp: Path) -> tuple[Path, str]:
    name = url.rsplit("/", 1)[-1]
    archive = temp / name
    checksum = temp / f"{name}.CHECKSUM"
    temp.mkdir(parents=True, exist_ok=True)
    if not archive.exists():
        urllib.request.urlretrieve(url, archive)
    if not checksum.exists():
        urllib.request.urlretrieve(url + ".CHECKSUM", checksum)
    expected = checksum.read_text(encoding="utf-8").split()[0].lower()
    actual = sha256(archive)
    if actual != expected:
        raise ValueError(f"checksum mismatch for {name}")
    return archive, actual


def load_bars(symbol: str, temp: Path) -> tuple[pd.DataFrame, list[dict]]:
    frames = []
    provenance = []
    names = [
        "open_time", "open", "high", "low", "close", "volume", "close_time",
        "quote_volume", "trade_count", "taker_buy_volume", "taker_buy_quote_volume", "ignore",
    ]
    for month in MONTHS:
        url = (
            "https://data.binance.vision/data/futures/um/monthly/klines/"
            f"{symbol}/1m/{symbol}-1m-{month}.zip"
        )
        archive, checksum = fetch_checked(url, temp)
        with zipfile.ZipFile(archive) as zipped, zipped.open(zipped.infolist()[0]) as handle:
            frame = pd.read_csv(handle, header=None, names=names)
        frame = frame[pd.to_numeric(frame.open_time, errors="coerce").notna()].copy()
        frame.open_time = frame.open_time.astype(np.int64)
        frames.append(frame)
        provenance.append({"symbol": symbol, "data_type": "kline_1m", "month": month, "sha256": checksum})
        archive.unlink()
        archive.with_suffix(archive.suffix + ".CHECKSUM").unlink()
    result = pd.concat(frames, ignore_index=True).sort_values("open_time")
    result = result.drop_duplicates("open_time", keep="last")
    result = result[
        result.open_time.ge(int(WARMUP_START.timestamp() * 1000))
        & result.open_time.lt(int(END.timestamp() * 1000))
    ].copy()
    expected = int((END - WARMUP_START).total_seconds() // 60)
    if len(result) != expected:
        raise ValueError(f"{symbol}: expected {expected} warmup+pilot bars, got {len(result)}")
    if np.any(np.diff(result.open_time.to_numpy(np.int64)) != 60_000):
        raise ValueError(f"{symbol}: incomplete 1m clock")
    return result, provenance


def load_funding(symbol: str, temp: Path) -> tuple[pd.DataFrame, dict]:
    url = (
        "https://data.binance.vision/data/futures/um/monthly/fundingRate/"
        f"{symbol}/{symbol}-fundingRate-2024-03.zip"
    )
    archive, checksum = fetch_checked(url, temp)
    with zipfile.ZipFile(archive) as zipped, zipped.open(zipped.infolist()[0]) as handle:
        frame = pd.read_csv(handle)
    archive.unlink()
    archive.with_suffix(archive.suffix + ".CHECKSUM").unlink()
    columns = {column.lower(): column for column in frame.columns}
    ts_col = columns.get("calc_time") or next(
        columns[key] for key in columns if "funding" in key and "time" in key
    )
    rate_col = next(columns[key] for key in columns if "funding" in key and "rate" in key)
    mark_col = next((columns[key] for key in columns if "mark" in key), None)
    result = pd.DataFrame(
        {
            "event_time_ns": pd.to_numeric(frame[ts_col]).astype(np.int64) * 1_000_000,
            "funding_rate": pd.to_numeric(frame[rate_col]).astype(float),
            "mark_price": pd.to_numeric(frame[mark_col]).astype(float) if mark_col else 0.0,
        }
    )
    result = result[
        result.event_time_ns.ge(int(START.value)) & result.event_time_ns.lt(int(END.value))
    ].sort_values("event_time_ns")
    return result, {"symbol": symbol, "data_type": "fundingRate", "month": "2024-03", "sha256": checksum}


def to_events(frame: pd.DataFrame, symbol: str) -> list[BarEvent]:
    instrument = f"{symbol}-PERP.BINANCE"
    return [
        BarEvent(
            instrument_id=instrument,
            event_time_ns=int(row.open_time) * 1_000_000,
            open=float(row.open),
            high=float(row.high),
            low=float(row.low),
            close=float(row.close),
            volume=float(row.volume),
            quote_volume=float(row.quote_volume),
            trade_count=int(row.trade_count),
        )
        for row in frame.itertuples(index=False)
    ]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, default=ROOT)
    parser.add_argument("--output", type=Path, default=ROOT / OUTPUT)
    parser.add_argument("--temp", type=Path, default=Path(r"D:\nautilus\outputs\tmp_l1_signal_data"))
    args = parser.parse_args()
    repo = args.repo.resolve()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    plan = pd.read_csv(repo / PILOT_PLAN)
    strategy_ids = plan.strategy_id.drop_duplicates().tolist()
    if len(strategy_ids) != 6 or len(plan) != 18:
        raise ValueError("frozen pilot scope changed")
    provenance = []

    for symbol in SYMBOLS:
        bars_frame, bar_provenance = load_bars(symbol, args.temp)
        funding, funding_provenance = load_funding(symbol, args.temp)
        provenance.extend(bar_provenance)
        provenance.append(funding_provenance)
        funding.to_parquet(output / f"funding_{symbol}.parquet", index=False, compression="zstd")
        all_events = to_events(bars_frame, symbol)
        live_mask = (bars_frame.open_time >= int(START.timestamp() * 1000)) & (
            bars_frame.open_time < int(END.timestamp() * 1000)
        )
        warmup_events = [event for event, live in zip(all_events, live_mask, strict=True) if not live]
        live_events = [event for event, live in zip(all_events, live_mask, strict=True) if live]
        execution = [
            replace(
                event,
                event_time_ns=event.event_time_ns + 60_000_000_000,
                open=event.close,
                high=event.close,
                low=event.close,
            )
            for event in live_events
        ]
        target = pd.DataFrame(
            {
                # ``run_decision_lifecycle`` materializes each completed-bar
                # decision onto this minute clock (00:01 uses the 00:00 bar).
                "decision_time_ns": [event.event_time_ns for event in live_events],
                "mark_close": [event.close for event in live_events],
            }
        )
        for strategy_id in strategy_ids:
            config = yaml.safe_load(
                (repo / "strategies" / strategy_id / "config.yaml").read_text(encoding="utf-8")
            ) or {}
            direction, _, metadata = run_decision_lifecycle(
                strategy_name=strategy_id,
                source_config=config,
                frequency="1m",
                lag_minutes=0,
                bars_1m=live_events,
                strategy_bars=build_strategy_clock(live_events, "1m"),
                end_exclusive_ns=int(END.value),
                warmup_bars=warmup_events,
                execution_events=execution,
            )
            target[strategy_id] = direction
            (output / f"target_meta_{strategy_id}_{symbol}.json").write_text(
                json.dumps(metadata, indent=2) + "\n", encoding="utf-8"
            )
        target.to_parquet(output / f"target_positions_{symbol}.parquet", index=False, compression="zstd")
    pd.DataFrame(provenance).to_csv(output / "signal_source_provenance.csv", index=False)
    if args.temp.exists() and not any(args.temp.iterdir()):
        args.temp.rmdir()


if __name__ == "__main__":
    main()
