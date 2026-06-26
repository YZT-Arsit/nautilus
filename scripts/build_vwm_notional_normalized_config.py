#!/usr/bin/env python3
"""Generate a notional-normalized VWM batch config (per-symbol order quantity).

Fixed ``quantity = 1.0`` makes the initial notional wildly different across
symbols (1 BTC contract >> 1 SOL contract), so cross-symbol return / drawdown
magnitudes are not comparable. This script sizes each symbol so its **initial
notional is ~equal**:

    order_quantity = target_notional_usdt / initial_price
    initial_price  = close of the FIRST 15m bar of the window (read from parquet)

It only **reads** local bar parquet (lazy pyarrow), computes sizes, and writes:

    <out-sizing>                      position_sizing.csv
    <out-config>                      the batch YAML (per-symbol `quantity`)

It does NOT run a backtest, hit the network, or modify VWM strategy code. Symbols
with no data are skipped in the config and recorded as ``status=missing_data`` in
the sizing CSV (never fabricated). The runner already threads a per-job
``quantity`` from each ``universe.include`` item into ``execution.quantity``; no
strategy-logic change is involved.
"""
from __future__ import annotations

import argparse
import csv
from pathlib import Path
from typing import Any, Callable

SIZING_METHOD = "initial_close_target_notional"
_CAVEAT = ("initial notional normalized to target; funding/liquidation/margin/"
           "mark-index not modeled; sizing fixed at window start (not rebalanced)")
SIZING_CSV_COLUMNS = ["symbol", "initial_price", "target_notional_usdt", "order_quantity",
                      "actual_initial_notional", "sizing_method", "status", "caveat"]


# --- pure sizing math -------------------------------------------------------

def order_quantity(target_notional_usdt: float, initial_price: float) -> float | None:
    """``target_notional / initial_price`` or None when price is non-positive."""
    try:
        ip = float(initial_price); tn = float(target_notional_usdt)
    except (TypeError, ValueError):
        return None
    if ip <= 0.0:
        return None
    return tn / ip


# --- parquet read (I/O boundary, lazy pyarrow) ------------------------------

def read_initial_close(data_root: Path, *, exchange: str, venue_type: str, symbol: str,
                       bar_type: str, start: str) -> float | None:
    """Close of the earliest bar on ``start`` for this symbol (None if absent)."""
    part_dir = (data_root / f"exchange={exchange}" / f"venue_type={venue_type}"
                / f"symbol={symbol}" / f"bar_type={bar_type}" / f"date={start}")
    if not part_dir.is_dir():
        return None
    try:
        import pyarrow.parquet as pq  # noqa: PLC0415
    except Exception:
        return None
    rows: list[tuple[Any, float]] = []
    for part in sorted(part_dir.glob("part-*.parquet")):
        try:
            t = pq.read_table(part, columns=["ts", "close"])
        except Exception:
            continue
        # ts may be int64 ns OR a timestamp scalar; keep it raw (it's sortable) and
        # only coerce close to float. Rows are already time-ordered per partition.
        for a, b in zip(t.column("ts").to_pylist(), t.column("close").to_pylist()):
            try:
                rows.append((a, float(b)))
            except (TypeError, ValueError):
                pass
    if not rows:
        return None
    rows.sort(key=lambda r: r[0])
    return rows[0][1]


# --- assembly ---------------------------------------------------------------

def build_sizing(symbols: list[str], *, exchange: str, venue_type: str, bar_type: str,
                 start: str, target_notional_usdt: float, data_root: Path,
                 price_reader: Callable[..., float | None] | None = None) -> list[dict]:
    """One sizing dict per symbol (status ok / missing_data)."""
    reader = price_reader or read_initial_close
    out = []
    for sym in symbols:
        price = reader(data_root, exchange=exchange, venue_type=venue_type, symbol=sym,
                       bar_type=bar_type, start=start)
        if price is None or price <= 0:
            out.append({"symbol": sym, "initial_price": "NA",
                        "target_notional_usdt": target_notional_usdt, "order_quantity": "NA",
                        "actual_initial_notional": "NA", "sizing_method": SIZING_METHOD,
                        "status": "missing_data", "caveat": "no bar at window start"})
            continue
        qty = order_quantity(target_notional_usdt, price)
        qty = round(qty, 8)
        out.append({"symbol": sym, "initial_price": price,
                    "target_notional_usdt": target_notional_usdt, "order_quantity": qty,
                    "actual_initial_notional": round(qty * price, 4),
                    "sizing_method": SIZING_METHOD, "status": "ok", "caveat": _CAVEAT})
    return out


def build_config(sizing_rows: list[dict], *, exchange: str, venue_type: str, bar_type: str,
                 start: str, end: str, initial_cash: float, data_root: str,
                 output_root: str) -> dict[str, Any]:
    """Batch config with one include entry per OK symbol carrying its quantity."""
    include = []
    for r in sizing_rows:
        if r.get("status") != "ok":
            continue
        sym = r["symbol"]
        include.append({
            "exchange": exchange, "venue_type": venue_type, "symbol": sym,
            "instrument_id": f"{sym}-PERP.BINANCE", "bar_type": bar_type,
            "quantity": float(r["order_quantity"]),
        })
    return {
        "strategy": {"name": "vwm",
                     "params": {"mom_len": 5, "avg_len": 20, "atr_len": 5,
                                "atr_pct": 0.5, "setup_len": 5}},
        "execution": {"backend": "nautilus", "fill_timing": "same_bar", "fee_rate": 0.0005,
                      "initial_cash": initial_cash, "quantity": 1.0, "sell_means": "short",
                      "allow_short": True, "price_field": "close", "slippage_bps": 1.0},
        "data": {"root": data_root, "start": start, "end": end, "bar_type": bar_type},
        "universe": {"include": include, "exclude": []},
        "output": {"root": output_root, "overwrite": False},
        "metadata": {"market_type": "crypto_perpetual", "contract_type": "usd_m_perpetual",
                     "data_source": "binance_vision_futures_um_klines",
                     "sizing_method": SIZING_METHOD,
                     "caveat": "notional_normalized_initial_close;funding_liquidation_margin_mark_index_not_modeled"},
    }


def write_sizing_csv(rows: list[dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=SIZING_CSV_COLUMNS)
        w.writeheader()
        for r in rows:
            w.writerow({c: r.get(c, "") for c in SIZING_CSV_COLUMNS})


def write_config_yaml(cfg: dict, path: Path) -> None:
    import yaml  # noqa: PLC0415
    path.parent.mkdir(parents=True, exist_ok=True)
    header = ("# Notional-normalized VWM batch (per-symbol order_quantity so each symbol's\n"
              "# initial notional ~= target_notional_usdt). Generated by\n"
              "# scripts/build_vwm_notional_normalized_config.py. VWM signal logic UNCHANGED;\n"
              "# only the per-job order size differs. CAVEAT: funding/liquidation/margin/\n"
              "# mark-index not modeled; sizing fixed at window start (not rebalanced).\n\n")
    path.write_text(header + yaml.safe_dump(cfg, sort_keys=False), encoding="utf-8")


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="Generate a notional-normalized VWM batch config")
    ap.add_argument("--data-root", default="historical_data/market_data")
    ap.add_argument("--out-config", required=True)
    ap.add_argument("--symbols", default="BTCUSDT,ETHUSDT,SOLUSDT,BNBUSDT")
    ap.add_argument("--exchange", default="BINANCE")
    ap.add_argument("--venue-type", default="futures_um")
    ap.add_argument("--bar-type", default="15m")
    ap.add_argument("--start", required=True)
    ap.add_argument("--end", required=True)
    ap.add_argument("--initial-cash", type=float, default=100000)
    ap.add_argument("--target-notional-usdt", type=float, default=10000)
    ap.add_argument("--out-sizing", required=True)
    ap.add_argument("--overwrite", action="store_true")
    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    out_config = Path(args.out_config)
    if out_config.exists() and not args.overwrite:
        print(f"REFUSING_OVERWRITE existing config {out_config} (pass --overwrite)")
        return 3
    symbols = [s.strip().upper() for s in str(args.symbols).split(",") if s.strip()]
    sizing = build_sizing(symbols, exchange=args.exchange, venue_type=args.venue_type,
                          bar_type=args.bar_type, start=args.start,
                          target_notional_usdt=args.target_notional_usdt,
                          data_root=Path(args.data_root))
    write_sizing_csv(sizing, Path(args.out_sizing))
    ok = [r for r in sizing if r["status"] == "ok"]
    if not ok:
        print("ERROR: no symbol had data at window start; no config written")
        return 2
    cfg = build_config(sizing, exchange=args.exchange, venue_type=args.venue_type,
                       bar_type=args.bar_type, start=args.start, end=args.end,
                       initial_cash=args.initial_cash, data_root=args.data_root,
                       output_root=str(Path(args.out_sizing).parent).replace("\\", "/"))
    write_config_yaml(cfg, out_config)
    print(f"SIZING_CSV {args.out_sizing}")
    print(f"OUT_CONFIG {out_config}")
    print(f"SYMBOLS ok={len(ok)} missing={len(sizing) - len(ok)} target_notional_usdt={args.target_notional_usdt}")
    for r in sizing:
        print(f"  {r['symbol']}: status={r['status']} initial_price={r['initial_price']} "
              f"order_quantity={r['order_quantity']} actual_notional={r['actual_initial_notional']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
