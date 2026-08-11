#!/usr/bin/env python3
"""
Compare two controlled intent lifecycles with native Nautilus fills and turnover.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path

import pandas as pd

from data_engine.loader import load_events
from strategy_framework.backends.nautilus_native import run_native_backtest
from strategy_framework.backends.nautilus_simulation import IntentFillSimulator
from strategy_framework.execution.intents import OrderIntent
from strategy_framework.execution.intents import PositionIntent


INSTRUMENT_ID = "BTCUSDT-PERP.BINANCE"
CAPITAL_USDT = 100_000.0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--market-root", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--start", default="2026-06-01")
    parser.add_argument("--end", default="2026-06-07")
    return parser.parse_args()


def load_bars(root: Path, start: str, end: str) -> list:
    _, events = load_events(
        {
            "mode": "hive_parquet_bars",
            "root": str(root),
            "instrument_id": INSTRUMENT_ID,
            "start": start,
            "end": end,
            "filters": {
                "asset_class": "crypto",
                "exchange": "BINANCE",
                "venue_type": "futures_um",
                "symbol": "BTCUSDT",
                "data_type": "bar",
                "freq": "1m",
            },
        }
    )
    return list(events)


def simulator_fills(bars: list, side: str, open_index: int, close_index: int, qty: float):
    simulator = IntentFillSimulator(default_price_field="close", allow_short=True)
    opening = OrderIntent(
        instrument_id=INSTRUMENT_ID,
        side=side,
        quantity=qty,
        event_time_ns=bars[open_index].event_time_ns,
        reason="controlled_open",
    )
    closing = PositionIntent(
        instrument_id=INSTRUMENT_ID,
        target="FLAT",
        quantity=0.0,
        event_time_ns=bars[close_index].event_time_ns,
        reason="controlled_close",
    )
    simulator.on_intent(opening, bars[open_index])
    simulator.on_intent(closing, bars[close_index])
    return simulator.report().fills


def turnover(fills) -> float:
    return sum(abs(fill.quantity) * fill.price for fill in fills) / CAPITAL_USDT


def run(args: argparse.Namespace) -> Path:
    args.output_dir.mkdir(parents=True, exist_ok=True)
    source = load_bars(args.market_root, args.start, args.end)
    bars = [
        {
            "event_time_ns": bar.event_time_ns,
            "open": bar.open,
            "high": bar.high,
            "low": bar.low,
            "close": bar.close,
            "volume": bar.volume,
        }
        for bar in source
    ]
    rows: list[dict] = []
    summaries: list[dict] = []
    for scenario, side, open_index, close_index in (
        ("controlled_long", "BUY", 10, 20),
        ("controlled_short", "SELL", 40, 50),
    ):
        quantity = 1.0
        intents = {
            source[open_index].event_time_ns: (side, quantity),
            source[close_index].event_time_ns: ("FLAT", 0.0),
        }
        native, native_summary = run_native_backtest(
            bars=bars,
            intents_by_ts=intents,
            instrument_id=INSTRUMENT_ID,
            quantity=quantity,
            initial_cash=CAPITAL_USDT,
            allow_short=True,
            fee_rate=0.0,
            slippage_bps=0.0,
            trader_id=f"AUDIT-{scenario.upper()}",
        )
        simulated = simulator_fills(source, side, open_index, close_index, quantity)
        maximum = max(len(native), len(simulated))
        for index in range(maximum):
            ours = simulated[index] if index < len(simulated) else None
            nau = native[index] if index < len(native) else None
            rows.append(
                {
                    "scenario": scenario,
                    "fill_number": index + 1,
                    "intent_time_ns": [source[open_index], source[close_index]][index].event_time_ns
                    if index < 2
                    else None,
                    "our_side": ours.side if ours else None,
                    "nautilus_side": nau.side if nau else None,
                    "our_quantity": ours.quantity if ours else None,
                    "nautilus_quantity": nau.quantity if nau else None,
                    "our_fill_time_ns": ours.event_time_ns if ours else None,
                    "nautilus_fill_time_ns": nau.event_time_ns if nau else None,
                    "our_fill_price": ours.price if ours else None,
                    "nautilus_fill_price": nau.price if nau else None,
                    "side_match": ours is not None and nau is not None and ours.side == nau.side,
                    "quantity_match": ours is not None
                    and nau is not None
                    and abs(ours.quantity - nau.quantity) <= 1e-12,
                    "time_match": ours is not None
                    and nau is not None
                    and ours.event_time_ns == nau.event_time_ns,
                    "price_match": ours is not None
                    and nau is not None
                    and abs(ours.price - nau.price) <= 1e-12,
                }
            )
        our_turnover = turnover(simulated)
        native_turnover = turnover(native)
        summaries.append(
            {
                "scenario": scenario,
                "our_fill_count": len(simulated),
                "nautilus_fill_count": len(native),
                "our_turnover_x": our_turnover,
                "nautilus_turnover_x": native_turnover,
                "turnover_abs_error": abs(our_turnover - native_turnover),
                "native_summary": native_summary,
            }
        )
    audit = pd.DataFrame(rows)
    for column in ("intent_time_ns", "our_fill_time_ns", "nautilus_fill_time_ns"):
        audit[column.removesuffix("_ns") + "_utc"] = pd.to_datetime(
            audit[column], unit="ns", utc=True
        )
    audit.to_csv(args.output_dir / "manual_order_nautilus_audit.csv", index=False)
    (args.output_dir / "manual_order_nautilus_summary.json").write_text(
        json.dumps(summaries, default=str, indent=2) + "\n", encoding="utf-8"
    )
    (args.output_dir / "manual_order_contract.json").write_text(
        json.dumps(
            {
                "capital_usdt": CAPITAL_USDT,
                "turnover_formula": "sum(abs(fill_quantity) * fill_price) / 100000",
                "rows": [asdict(fill) for fill in simulator_fills(source, "BUY", 10, 20, 1.0)],
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return args.output_dir


if __name__ == "__main__":
    print(run(parse_args()))
