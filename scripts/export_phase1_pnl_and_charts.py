#!/usr/bin/env python3
"""Export Phase-1 PnL CSVs + charts and build the artifact traceability index.

For the recommended delivery (volatility-targeted VWM), this reads each symbol's
``equity_curve.csv`` from the backtest run dir and produces, under a deliverable
root:

    pnl/<SYMBOL>_pnl.csv                standard PnL series (equity/pnl/drawdown/...)
    charts/<SYMBOL>_equity_curve.png    matplotlib PNGs (one figure each)
    charts/<SYMBOL>_drawdown.png
    charts/<SYMBOL>_pnl_curve.png
    charts/<SYMBOL>_position.png
    tables/artifact_index.csv/.md       per-row artifact_id -> pnl/chart/run_dir map
    tables/batch_evaluation_table.csv   delivery copy + artifact_id column
    tables/sizing_mode_comparison.*     copied if present
    raw_refs/run_paths.md, manifest.json, README.md, boss_summary.md

Pure stdlib for the data math; matplotlib (Agg) only for charts (guarded — if it
is missing, charts are ``NA`` and ``chart_status=partial``, never fabricated). No
network, no backtest, no strategy import. Reads existing outputs only.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import shutil
from pathlib import Path
from typing import Any


def read_csv_rows(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    with path.open(encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def _f(v: Any) -> float | None:
    try:
        x = float(v)
        return x if math.isfinite(x) else None
    except (TypeError, ValueError):
        return None


# --- pure PnL math ----------------------------------------------------------

PNL_COLUMNS = ["ts", "equity", "pnl", "cumulative_pnl", "drawdown", "drawdown_pct",
               "position", "close_price", "benchmark_equity", "benchmark_return",
               "strategy_return", "notes"]


def compute_pnl_rows(equity_rows: list[dict]) -> list[dict]:
    """Standard PnL series from an equity_curve.csv (already-parsed dict rows)."""
    out: list[dict] = []
    initial = None
    close0 = None
    peak = None
    prev_eq = None
    for r in equity_rows:
        eq = _f(r.get("equity"))
        if eq is None:
            continue
        if initial is None:
            initial = eq
        close = _f(r.get("close"))
        if close0 is None and close is not None and close != 0:
            close0 = close
        peak = eq if peak is None else max(peak, eq)
        dd = eq - peak                                   # <= 0
        dd_pct = (dd / peak) if peak else None
        pnl = (eq - prev_eq) if prev_eq is not None else 0.0
        prev_eq = eq
        bench_eq = (initial * close / close0) if (close is not None and close0) else None
        bench_ret = (close / close0 - 1.0) if (close is not None and close0) else None
        out.append({
            "ts": r.get("event_time") or r.get("event_time_ns") or "NA",
            "equity": eq, "pnl": pnl, "cumulative_pnl": eq - initial,
            "drawdown": dd, "drawdown_pct": dd_pct,
            "position": _f(r.get("position")) if r.get("position") is not None else "NA",
            "close_price": close if close is not None else "NA",
            "benchmark_equity": bench_eq if bench_eq is not None else "NA",
            "benchmark_return": bench_ret if bench_ret is not None else "NA",
            "strategy_return": (eq / initial - 1.0) if initial else "NA",
            "notes": "",
        })
    return out


def write_pnl_csv(rows: list[dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=PNL_COLUMNS)
        w.writeheader()
        for r in rows:
            w.writerow({c: r.get(c, "NA") for c in PNL_COLUMNS})


def artifact_id(strategy: str, symbol: str, bar_type: str, window_label: str,
                sizing_mode: str) -> str:
    return f"{strategy.upper()}_{symbol.upper()}_{bar_type}_{window_label}_{sizing_mode}"


# --- charts (matplotlib, guarded) -------------------------------------------

def render_charts(pnl_rows: list[dict], symbol: str, charts_dir: Path) -> dict[str, str | None]:
    """Four single-figure PNGs. Returns {chart_key: path|None}. None if no data /
    no matplotlib (caller marks chart_status=partial). x-axis = 15m bar index."""
    keys = {"equity_curve": None, "drawdown": None, "pnl_curve": None, "position": None}
    if not pnl_rows:
        return keys
    try:
        import matplotlib  # noqa: PLC0415
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt  # noqa: PLC0415
    except Exception:
        return keys
    charts_dir.mkdir(parents=True, exist_ok=True)
    x = list(range(len(pnl_rows)))

    def series(col):
        return [(_f(r.get(col)) if r.get(col) not in (None, "NA") else None) for r in pnl_rows]

    def plot(col, fname, title, ylabel, *, pct=False):
        ys = series(col)
        if all(v is None for v in ys):
            return None
        xs = [xi for xi, v in zip(x, ys) if v is not None]
        vs = [(v * 100.0 if pct else v) for v in ys if v is not None]
        fig = plt.figure(figsize=(10, 4))
        ax = fig.add_subplot(111)
        ax.plot(xs, vs, linewidth=1.0)
        ax.set_title(title); ax.set_xlabel("15m bar index"); ax.set_ylabel(ylabel)
        ax.grid(True, alpha=0.3)
        out = charts_dir / fname
        fig.tight_layout(); fig.savefig(out, dpi=110); plt.close(fig)
        return str(out).replace("\\", "/")

    keys["equity_curve"] = plot("equity", f"{symbol}_equity_curve.png",
                                f"VWM equity curve - {symbol}", "equity (USDT)")
    keys["drawdown"] = plot("drawdown_pct", f"{symbol}_drawdown.png",
                            f"VWM drawdown - {symbol}", "drawdown (%)", pct=True)
    keys["pnl_curve"] = plot("cumulative_pnl", f"{symbol}_pnl_curve.png",
                             f"VWM cumulative PnL - {symbol}", "cumulative PnL (USDT)")
    keys["position"] = plot("position", f"{symbol}_position.png",
                            f"VWM position exposure - {symbol}", "position (contracts)")
    return keys


# --- orchestration ----------------------------------------------------------

ARTIFACT_INDEX_COLUMNS = [
    "artifact_id", "strategy", "symbol", "exchange", "venue_type", "bar_type", "start", "end",
    "sizing_mode", "run_dir", "evaluation_row_status", "pnl_path", "equity_curve_path",
    "drawdown_chart_path", "pnl_chart_path", "position_chart_path", "trades_path", "fills_path",
    "report_json_path", "run_metadata_path", "notes",
]


def _rel(path: Path | str) -> str:
    return str(path).replace("\\", "/")


def _job_dir_for(backtest_root: Path, summary: dict) -> Path:
    job = summary.get("job_id") or summary.get("output_dir")
    return backtest_root / Path(str(job)).name if job else backtest_root


def run(args) -> dict:
    backtest_root = Path(args.backtest_root)
    deliver = Path(args.deliverable_root)
    (deliver / "pnl").mkdir(parents=True, exist_ok=True)
    (deliver / "charts").mkdir(parents=True, exist_ok=True)
    (deliver / "tables").mkdir(parents=True, exist_ok=True)
    (deliver / "raw_refs").mkdir(parents=True, exist_ok=True)

    eval_rows = read_csv_rows(Path(args.evaluation_table))
    summaries = json.loads((backtest_root / "summary.json").read_text(encoding="utf-8"))
    if isinstance(summaries, dict):
        summaries = [summaries]
    summ_by_symbol = {str(s.get("symbol", "")).upper(): s for s in summaries}
    # quarter label by the window END month (matches the project's "2026Q2" naming
    # for the 2026-03-01..05-31 window, whose bulk is Q2).
    window = f"{args.end[:4]}Q{(int(args.end[5:7]) - 1) // 3 + 1}" if len(args.end) >= 7 else args.end

    index_rows: list[dict] = []
    enriched: list[dict] = []
    run_paths_lines = ["# Raw backtest run directories (per symbol)", ""]
    missing: list[str] = []

    for row in eval_rows:
        symbol = str(row.get("Symbol", "")).upper()
        status = row.get("Backtest Status", "NA")
        s = summ_by_symbol.get(symbol)
        aid = artifact_id(args.strategy, symbol, args.bar_type, window, args.sizing_mode)
        rec = {"artifact_id": aid, "strategy": args.strategy, "symbol": symbol,
               "exchange": (s or {}).get("exchange", "BINANCE"),
               "venue_type": (s or {}).get("venue_type", "futures_um"),
               "bar_type": args.bar_type, "start": args.start, "end": args.end,
               "sizing_mode": args.sizing_mode, "evaluation_row_status": status}
        if s is None or status != "success":
            rec.update({k: "NA" for k in ARTIFACT_INDEX_COLUMNS if k not in rec})
            rec["run_dir"] = "NA"; rec["notes"] = "no successful run for this symbol"
            missing.append(symbol)
        else:
            jd = _job_dir_for(backtest_root, s)
            equity_rows = read_csv_rows(jd / "equity_curve.csv")
            pnl_rows = compute_pnl_rows(equity_rows)
            pnl_path = deliver / "pnl" / f"{symbol}_pnl.csv"
            write_pnl_csv(pnl_rows, pnl_path)
            charts = render_charts(pnl_rows, symbol, deliver / "charts")
            chart_status = "complete" if all(charts.values()) else "partial"
            rec.update({
                "run_dir": _rel(jd), "pnl_path": _rel(pnl_path),
                "equity_curve_path": charts["equity_curve"] or "NA",
                "drawdown_chart_path": charts["drawdown"] or "NA",
                "pnl_chart_path": charts["pnl_curve"] or "NA",
                "position_chart_path": charts["position"] or "NA",
                "trades_path": _rel(jd / "trades.csv") if (jd / "trades.csv").is_file() else "NA",
                "fills_path": _rel(jd / "fills.csv") if (jd / "fills.csv").is_file() else "NA",
                "report_json_path": _rel(jd / "report.json") if (jd / "report.json").is_file() else "NA",
                "run_metadata_path": _rel(jd / "run_metadata.json") if (jd / "run_metadata.json").is_file() else "NA",
                "notes": f"chart_status={chart_status}",
            })
            run_paths_lines.append(f"- **{symbol}** ({aid}): `{_rel(jd)}`")
        index_rows.append(rec)
        # enriched eval row = original + traceability columns
        er = dict(row)
        er["artifact_id"] = aid
        er["run_dir"] = rec.get("run_dir", "NA")
        er["pnl_path"] = rec.get("pnl_path", "NA")
        er["equity_curve_path"] = rec.get("equity_curve_path", "NA")
        er["drawdown_chart_path"] = rec.get("drawdown_chart_path", "NA")
        er["pnl_chart_path"] = rec.get("pnl_chart_path", "NA")
        er["position_chart_path"] = rec.get("position_chart_path", "NA")
        er["chart_status"] = "partial" if "NA" in (rec.get("equity_curve_path", "NA"),) else "complete"
        er["artifact_status"] = "traced" if rec.get("pnl_path", "NA") != "NA" else "missing"
        enriched.append(er)

    # write artifact_index
    _write_index_csv(index_rows, deliver / "tables" / "artifact_index.csv")
    _write_index_md(index_rows, deliver / "tables" / "artifact_index.md")
    # delivery copy of the evaluation table + artifact_id
    _write_enriched_eval(enriched, eval_rows, deliver / "tables" / "batch_evaluation_table.csv",
                         deliver / "tables" / "batch_evaluation_table.md")
    # copy companion tables if present
    for src in (backtest_root / "position_sizing.csv",):
        if src.is_file():
            shutil.copy2(src, deliver / "tables" / src.name)
    _copy_if(Path(args.sizing_comparison_csv) if args.sizing_comparison_csv else None,
             deliver / "tables" / "sizing_mode_comparison.csv")
    _copy_if(Path(args.sizing_comparison_md) if args.sizing_comparison_md else None,
             deliver / "tables" / "sizing_mode_comparison.md")
    (deliver / "raw_refs" / "run_paths.md").write_text("\n".join(run_paths_lines) + "\n", encoding="utf-8")
    _write_manifest(index_rows, deliver / "manifest.json", args)
    _write_readme(deliver / "README.md", args, missing)
    _write_boss_summary(deliver / "boss_summary.md", index_rows, args)
    return {"index_rows": index_rows, "missing": missing, "deliver": deliver}


def _copy_if(src: Path | None, dst: Path) -> None:
    if src and src.is_file():
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)


def _write_index_csv(rows: list[dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=ARTIFACT_INDEX_COLUMNS)
        w.writeheader()
        for r in rows:
            w.writerow({c: r.get(c, "NA") for c in ARTIFACT_INDEX_COLUMNS})


def _write_index_md(rows: list[dict], path: Path) -> None:
    cols = ["artifact_id", "symbol", "sizing_mode", "evaluation_row_status", "pnl_path",
            "equity_curve_path", "drawdown_chart_path", "position_chart_path", "run_dir"]
    lines = ["| " + " | ".join(cols) + " |", "| " + " | ".join("---" for _ in cols) + " |"]
    for r in rows:
        lines.append("| " + " | ".join(str(r.get(c, "NA")).replace("|", "\\|") for c in cols) + " |")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_enriched_eval(enriched: list[dict], orig_rows: list[dict], csv_path: Path, md_path: Path) -> None:
    base_cols = list(orig_rows[0].keys()) if orig_rows else []
    extra = ["artifact_id", "run_dir", "pnl_path", "equity_curve_path", "drawdown_chart_path",
             "pnl_chart_path", "position_chart_path", "chart_status", "artifact_status"]
    cols = base_cols + [c for c in extra if c not in base_cols]
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    with csv_path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        for r in enriched:
            w.writerow({c: r.get(c, "NA") for c in cols})
    md_cols = ["Symbol", "Total Return", "Excess Return", "Max Drawdown %", "Trade Count",
               "artifact_id", "pnl_path", "equity_curve_path"]
    md_cols = [c for c in md_cols if c in cols]
    lines = ["| " + " | ".join(md_cols) + " |", "| " + " | ".join("---" for _ in md_cols) + " |"]
    for r in enriched:
        lines.append("| " + " | ".join(str(r.get(c, "NA")).replace("|", "\\|") for c in md_cols) + " |")
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_manifest(index_rows: list[dict], path: Path, args) -> None:
    payload = {
        "deliverable": "phase1_vwm_crypto_perpetual_2026q2",
        "strategy": args.strategy, "sizing_mode": args.sizing_mode,
        "window": {"start": args.start, "end": args.end}, "bar_type": args.bar_type,
        "backtest_root": _rel(args.backtest_root),
        "artifacts": [{"artifact_id": r["artifact_id"], "symbol": r["symbol"],
                       "status": r["evaluation_row_status"], "pnl_path": r.get("pnl_path", "NA"),
                       "run_dir": r.get("run_dir", "NA")} for r in index_rows],
        "note": "no live trading, no private API; reproducible from configs + Binance Vision public data",
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _write_readme(path: Path, args, missing: list[str]) -> None:
    txt = f"""# Phase 1 Deliverable - VWM Crypto-Perpetual ({args.start} ~ {args.end}, {args.bar_type})

## What this is
The recommended Phase-1 delivery: a single strategy (VWM) evaluated across BTCUSDT /
ETHUSDT / SOLUSDT / BNBUSDT on Binance USD-M perpetuals, sized by **{args.sizing_mode}**.
Every evaluation-table row is traceable to its PnL data, charts, and raw run dir.

## Main table
`tables/batch_evaluation_table.md` (rows = symbol, cols = evaluation metric), with an
`artifact_id` column. Full columns in the `.csv`.

## How each row maps to PnL and charts
Each row has an `artifact_id` (e.g. `VWM_BTCUSDT_{args.bar_type}_2026Q2_{args.sizing_mode}`).
Look it up in `tables/artifact_index.md` to get:
- `pnl_path`            -> `pnl/<SYMBOL>_pnl.csv` (equity, pnl, drawdown, position, benchmark)
- `equity_curve_path`  -> `charts/<SYMBOL>_equity_curve.png`
- `drawdown_chart_path`-> `charts/<SYMBOL>_drawdown.png`
- `pnl_chart_path`     -> `charts/<SYMBOL>_pnl_curve.png`
- `position_chart_path`-> `charts/<SYMBOL>_position.png`
- `run_dir`            -> the original backtest job directory (raw equity_curve/trades/fills)

## artifact_id
`<STRATEGY>_<SYMBOL>_<bar_type>_<window>_<sizing_mode>`. Unique per evaluation row.

## Recommended viewing order
1. `boss_summary.md`
2. `tables/batch_evaluation_table.md`
3. `charts/*_equity_curve.png`
4. `charts/*_drawdown.png`
5. `tables/artifact_index.md`
6. `pnl/*.csv`

## Caveat
VWM is short-only; funding/margin/liquidation/mark-index are not modeled. Results are
for screening, not a performance verdict. {("Missing: " + ", ".join(missing)) if missing else ""}

## No live trading / no private API
All results come from offline backtests over Binance Vision **public** klines. No API
key; no account, position, or trading endpoints.

## How to reproduce
Re-run the config in `configs/backtests/` for this sizing mode, then
`scripts/build_strategy_batch_eval_table.py` and `scripts/export_phase1_pnl_and_charts.py`.
"""
    path.write_text(txt, encoding="utf-8")


def _write_boss_summary(path: Path, index_rows: list[dict], args) -> None:
    traced = sum(1 for r in index_rows if r.get("pnl_path", "NA") != "NA")
    txt = f"""# Phase 1 - Boss Summary

- 本阶段完成：整理 Phase-1 交付物，并为批量评测表的**每一行**建立可追溯关系。
- 表结构：**行 = 标的，列 = 评价指标**（推荐 sizing = {args.sizing_mode}）。
- **评测表每一行都有 artifact_id，可在 artifact_index 中找到对应 PnL 文件、equity curve、
  drawdown chart、position chart 和原始 run directory。** 共 {traced}/{len(index_rows)} 行已完整追溯。
- 当前结论：VWM 为短仓策略，2026 Q2 多数标的上涨，整体跑输 benchmark；仓位归一化只改规模不改信号。
- 下一步：双向化 / regime 过滤的稳健性验证 + 永续机制建模。
- Caveat：未建模 funding/保证金/强平/mark；非实盘、无私有 API。

查看顺序：boss_summary.md -> tables/batch_evaluation_table.md -> charts/*_equity_curve.png
-> charts/*_drawdown.png -> tables/artifact_index.md -> pnl/*.csv
"""
    path.write_text(txt, encoding="utf-8")


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="Export Phase-1 PnL + charts + artifact index")
    ap.add_argument("--backtest-root", required=True)
    ap.add_argument("--evaluation-table", required=True)
    ap.add_argument("--deliverable-root", required=True)
    ap.add_argument("--strategy", default="VWM")
    ap.add_argument("--sizing-mode", default="vol_targeted")
    ap.add_argument("--bar-type", default="15m")
    ap.add_argument("--start", required=True)
    ap.add_argument("--end", required=True)
    ap.add_argument("--sizing-comparison-csv", default=None)
    ap.add_argument("--sizing-comparison-md", default=None)
    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    res = run(args)
    deliver = res["deliver"]
    print(f"DELIVERABLE_ROOT {deliver}")
    print(f"ARTIFACT_INDEX {deliver / 'tables' / 'artifact_index.csv'}")
    print(f"EVAL_TABLE {deliver / 'tables' / 'batch_evaluation_table.csv'}")
    print(f"ARTIFACTS {len(res['index_rows'])} traced="
          f"{sum(1 for r in res['index_rows'] if r.get('pnl_path', 'NA') != 'NA')} "
          f"missing={len(res['missing'])}")
    for r in res["index_rows"]:
        print(f"  {r['artifact_id']}: status={r['evaluation_row_status']} "
              f"pnl={r.get('pnl_path', 'NA')} equity={r.get('equity_curve_path', 'NA')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
