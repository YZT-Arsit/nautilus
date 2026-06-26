#!/usr/bin/env python3
"""Generate a VWM batch config with per-symbol order sizing (two modes).

Sizing modes (the strategy signal is identical in every case — only the order
size changes; this is a config passthrough, NOT a VWM-logic change):

* ``notional``      -- equal initial notional:
                       order_quantity = target_notional_usdt / initial_price
* ``realized_vol``  -- equal per-bar risk budget:
                       order_quantity = target_risk_usdt_per_bar
                                        / (initial_price * realized_vol_15m)
                       realized_vol_15m = std of 15m log returns over the window.
                       High-vol symbols get fewer units, low-vol more, so each
                       symbol risks ~the same USDT per 15m bar.

Only **reads** local bar parquet (lazy pyarrow), computes sizes, and writes a
``position_sizing.csv`` plus the batch YAML (each ``universe.include`` carries its
``quantity``; the runner threads that into ``execution.quantity``). No backtest,
no network, no private endpoint, no VWM edit. Defensive statuses
(``missing_data`` / ``insufficient_data`` / ``failed_zero_vol`` /
``capped_max_notional`` / ``below_min``) are recorded, never fabricated.
"""
from __future__ import annotations

import argparse
import csv
import math
from pathlib import Path
from typing import Any, Callable

# reuse the notional helpers (single source of truth for initial-close + qty math)
from scripts.build_vwm_notional_normalized_config import (
    order_quantity,
    read_initial_close,
)

NA = "NA"
SIZING_CSV_COLUMNS = [
    "symbol", "initial_price", "realized_vol_15m", "target_notional_usdt",
    "target_risk_usdt_per_bar", "raw_order_quantity", "raw_initial_notional",
    "final_order_quantity", "final_initial_notional", "min_notional_usdt",
    "max_notional_usdt", "sizing_method", "sizing_status", "caveat",
]
_BASE_CAVEAT = ("funding/liquidation/margin/mark-index not modeled; sizing fixed at "
                "window start (not rebalanced)")


# --- realized-volatility math -----------------------------------------------

def realized_vol(closes: list[float]) -> float | None:
    """Population std of 15m log returns; None if < 2 returns or bad data."""
    cs = [float(c) for c in closes if isinstance(c, (int, float)) and math.isfinite(float(c)) and c > 0]
    if len(cs) < 3:
        return None
    rets = [math.log(cs[i] / cs[i - 1]) for i in range(1, len(cs))]
    n = len(rets)
    mean = sum(rets) / n
    var = sum((r - mean) ** 2 for r in rets) / n
    return math.sqrt(var)


# --- parquet read (I/O boundary, lazy pyarrow) ------------------------------

def read_window_closes(data_root: Path, *, exchange: str, venue_type: str, symbol: str,
                       bar_type: str, start: str, end: str) -> list[float]:
    """Time-ordered closes over [start, end] (empty if absent). ts kept raw."""
    base = (data_root / f"exchange={exchange}" / f"venue_type={venue_type}"
            / f"symbol={symbol}" / f"bar_type={bar_type}")
    if not base.is_dir():
        return []
    try:
        import pyarrow.parquet as pq  # noqa: PLC0415
    except Exception:
        return []
    rows: list[tuple[Any, float]] = []
    for part in sorted(base.glob("date=*/part-*.parquet")):
        day = part.parent.name.replace("date=", "")
        if not (start <= day <= end):
            continue
        try:
            t = pq.read_table(part, columns=["ts", "close"])
        except Exception:
            continue
        for a, b in zip(t.column("ts").to_pylist(), t.column("close").to_pylist()):
            try:
                rows.append((a, float(b)))
            except (TypeError, ValueError):
                pass
    rows.sort(key=lambda r: r[0])
    return [c for _, c in rows]


# --- sizing assembly --------------------------------------------------------

def _row(symbol, *, method, initial_price=NA, realized=NA, target_notional=NA,
         target_risk=NA, raw_qty=NA, raw_notional=NA, final_qty=NA, final_notional=NA,
         min_notional=NA, max_notional=NA, status, caveat):
    return {"symbol": symbol, "initial_price": initial_price, "realized_vol_15m": realized,
            "target_notional_usdt": target_notional, "target_risk_usdt_per_bar": target_risk,
            "raw_order_quantity": raw_qty, "raw_initial_notional": raw_notional,
            "final_order_quantity": final_qty, "final_initial_notional": final_notional,
            "min_notional_usdt": min_notional, "max_notional_usdt": max_notional,
            "sizing_method": method, "sizing_status": status, "caveat": caveat}


def build_sizing(symbols: list[str], *, mode: str, exchange: str, venue_type: str,
                 bar_type: str, start: str, end: str, target_notional_usdt: float,
                 target_risk_usdt_per_bar: float, min_notional_usdt: float,
                 max_notional_usdt: float, data_root: Path,
                 price_reader: Callable[..., float | None] | None = None,
                 closes_reader: Callable[..., list[float]] | None = None) -> list[dict]:
    """One sizing dict per symbol for the chosen mode."""
    price_reader = price_reader or read_initial_close
    closes_reader = closes_reader or read_window_closes
    method = "initial_close_target_notional" if mode == "notional" else "realized_vol_target"
    out = []
    for sym in symbols:
        if mode == "notional":
            price = price_reader(data_root, exchange=exchange, venue_type=venue_type,
                                 symbol=sym, bar_type=bar_type, start=start)
            if price is None or price <= 0:
                out.append(_row(sym, method=method, target_notional=target_notional_usdt,
                                status="missing_data", caveat="no bar at window start"))
                continue
            qty = round(order_quantity(target_notional_usdt, price), 8)
            out.append(_row(sym, method=method, initial_price=price,
                            target_notional=target_notional_usdt, raw_qty=qty,
                            raw_notional=round(qty * price, 4), final_qty=qty,
                            final_notional=round(qty * price, 4), status="ok",
                            caveat="initial notional normalized to target; " + _BASE_CAVEAT))
            continue

        # realized_vol mode
        closes = closes_reader(data_root, exchange=exchange, venue_type=venue_type,
                               symbol=sym, bar_type=bar_type, start=start, end=end)
        if not closes:
            out.append(_row(sym, method=method, target_risk=target_risk_usdt_per_bar,
                            min_notional=min_notional_usdt, max_notional=max_notional_usdt,
                            status="missing_data", caveat="no bars in window"))
            continue
        if len(closes) < 3:
            out.append(_row(sym, method=method, initial_price=closes[0],
                            target_risk=target_risk_usdt_per_bar, min_notional=min_notional_usdt,
                            max_notional=max_notional_usdt, status="insufficient_data",
                            caveat="fewer than 3 closes; cannot estimate vol"))
            continue
        price = closes[0]
        rv = realized_vol(closes)
        if rv is None or rv <= 0 or price <= 0:
            out.append(_row(sym, method=method, initial_price=price, realized=(rv if rv else NA),
                            target_risk=target_risk_usdt_per_bar, min_notional=min_notional_usdt,
                            max_notional=max_notional_usdt, status="failed_zero_vol",
                            caveat="realized vol <= 0; cannot size"))
            continue
        raw_qty = target_risk_usdt_per_bar / (price * rv)
        raw_notional = raw_qty * price                       # = target_risk / rv
        status = "ok"
        caveat = "per-bar risk budget normalized to target; " + _BASE_CAVEAT
        final_notional = raw_notional
        if raw_notional > max_notional_usdt:
            final_notional = max_notional_usdt
            status = "capped_max_notional"
            caveat = f"raw notional {raw_notional:.0f} > max {max_notional_usdt:.0f}, capped; " + _BASE_CAVEAT
        elif raw_notional < min_notional_usdt:
            final_notional = min_notional_usdt
            status = "below_min"
            caveat = f"raw notional {raw_notional:.0f} < min {min_notional_usdt:.0f}, raised to min; " + _BASE_CAVEAT
        final_qty = round(final_notional / price, 8)
        out.append(_row(sym, method=method, initial_price=round(price, 6),
                        realized=round(rv, 8), target_risk=target_risk_usdt_per_bar,
                        raw_qty=round(raw_qty, 8), raw_notional=round(raw_notional, 4),
                        final_qty=final_qty, final_notional=round(final_qty * price, 4),
                        min_notional=min_notional_usdt, max_notional=max_notional_usdt,
                        status=status, caveat=caveat))
    return out


def build_config(sizing_rows: list[dict], *, exchange: str, venue_type: str, bar_type: str,
                 start: str, end: str, initial_cash: float, data_root: str,
                 output_root: str, sizing_method: str) -> dict[str, Any]:
    include = []
    for r in sizing_rows:
        if r.get("sizing_status") in ("missing_data", "insufficient_data", "failed_zero_vol"):
            continue
        sym = r["symbol"]
        include.append({"exchange": exchange, "venue_type": venue_type, "symbol": sym,
                        "instrument_id": f"{sym}-PERP.BINANCE", "bar_type": bar_type,
                        "quantity": float(r["final_order_quantity"])})
    return {
        "strategy": {"name": "vwm", "params": {"mom_len": 5, "avg_len": 20, "atr_len": 5,
                                               "atr_pct": 0.5, "setup_len": 5}},
        "execution": {"backend": "nautilus", "fill_timing": "same_bar", "fee_rate": 0.0005,
                      "initial_cash": initial_cash, "quantity": 1.0, "sell_means": "short",
                      "allow_short": True, "price_field": "close", "slippage_bps": 1.0},
        "data": {"root": data_root, "start": start, "end": end, "bar_type": bar_type},
        "universe": {"include": include, "exclude": []},
        "output": {"root": output_root, "overwrite": False},
        "metadata": {"market_type": "crypto_perpetual", "contract_type": "usd_m_perpetual",
                     "data_source": "binance_vision_futures_um_klines",
                     "sizing_method": sizing_method,
                     "caveat": "per_symbol_sizing;funding_liquidation_margin_mark_index_not_modeled"},
    }


def write_sizing_csv(rows: list[dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=SIZING_CSV_COLUMNS)
        w.writeheader()
        for r in rows:
            w.writerow({c: r.get(c, "") for c in SIZING_CSV_COLUMNS})


def write_config_yaml(cfg: dict, path: Path, *, mode: str) -> None:
    import yaml  # noqa: PLC0415
    path.parent.mkdir(parents=True, exist_ok=True)
    header = (f"# VWM batch, per-symbol sizing (mode={mode}). Generated by\n"
              "# scripts/build_vwm_sizing_config.py. VWM signal logic UNCHANGED; only the\n"
              "# per-job order size differs. CAVEAT: funding/liquidation/margin/mark-index\n"
              "# not modeled; sizing fixed at window start (not rebalanced).\n\n")
    path.write_text(header + yaml.safe_dump(cfg, sort_keys=False), encoding="utf-8")


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="Generate a per-symbol-sized VWM batch config")
    ap.add_argument("--data-root", default="historical_data/market_data")
    ap.add_argument("--out-config", required=True)
    ap.add_argument("--symbols", default="BTCUSDT,ETHUSDT,SOLUSDT,BNBUSDT")
    ap.add_argument("--exchange", default="BINANCE")
    ap.add_argument("--venue-type", default="futures_um")
    ap.add_argument("--bar-type", default="15m")
    ap.add_argument("--start", required=True)
    ap.add_argument("--end", required=True)
    ap.add_argument("--initial-cash", type=float, default=100000)
    ap.add_argument("--sizing-mode", choices=["notional", "realized_vol"], default="notional")
    ap.add_argument("--target-notional-usdt", type=float, default=10000)
    ap.add_argument("--target-risk-usdt-per-bar", type=float, default=50)
    ap.add_argument("--min-notional-usdt", type=float, default=1000)
    ap.add_argument("--max-notional-usdt", type=float, default=20000)
    ap.add_argument("--vol-lookback-mode", choices=["full_window"], default="full_window")
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
    sizing = build_sizing(symbols, mode=args.sizing_mode, exchange=args.exchange,
                          venue_type=args.venue_type, bar_type=args.bar_type, start=args.start,
                          end=args.end, target_notional_usdt=args.target_notional_usdt,
                          target_risk_usdt_per_bar=args.target_risk_usdt_per_bar,
                          min_notional_usdt=args.min_notional_usdt,
                          max_notional_usdt=args.max_notional_usdt, data_root=Path(args.data_root))
    write_sizing_csv(sizing, Path(args.out_sizing))
    method = "initial_close_target_notional" if args.sizing_mode == "notional" else "realized_vol_target"
    usable = [r for r in sizing if r["sizing_status"] in ("ok", "capped_max_notional", "below_min")]
    if not usable:
        print("ERROR: no symbol could be sized; no config written")
        return 2
    cfg = build_config(sizing, exchange=args.exchange, venue_type=args.venue_type,
                       bar_type=args.bar_type, start=args.start, end=args.end,
                       initial_cash=args.initial_cash, data_root=args.data_root,
                       output_root=str(Path(args.out_sizing).parent).replace("\\", "/"),
                       sizing_method=method)
    write_config_yaml(cfg, out_config, mode=args.sizing_mode)
    print(f"SIZING_CSV {args.out_sizing}")
    print(f"OUT_CONFIG {out_config}")
    print(f"MODE {args.sizing_mode} usable={len(usable)} target_risk={args.target_risk_usdt_per_bar} "
          f"target_notional={args.target_notional_usdt}")
    for r in sizing:
        print(f"  {r['symbol']}: status={r['sizing_status']} vol={r['realized_vol_15m']} "
              f"final_qty={r['final_order_quantity']} final_notional={r['final_initial_notional']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
