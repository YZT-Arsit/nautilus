#!/usr/bin/env python3
"""Build the run_uid-anchored result data layer for the Phase-1 VWM delivery.

Reads existing backtest outputs (never re-runs a backtest) and, for each
successful symbol, produces under the deliverable root:

    pnl/<run_uid>_pnl.csv              per-run standard PnL timeseries
    pnl/pnl_timeseries.csv             all runs concatenated (run_uid keyed)
    charts/<run_uid>_*.png             equity / drawdown / pnl / position / benchmark
    tables/artifact_manifest.csv/.md   run_uid -> every artifact path + source
    tables/batch_evaluation_table_with_uid.csv/.md   main table + run_uid + paths
    manifest.json                      run-level manifest (identity field sources)
    dashboard.html                     static, dependency-free local dashboard
    README.md, boss_summary.md         boss-facing docs

run_uid comes from :mod:`research.backtest_artifacts` -- deterministic, stable,
independent of file mtimes. Pure stdlib for the data math; matplotlib (Agg) only
for charts (guarded; missing -> chart path NA + status partial, never fabricated).
No network, no private endpoint, no strategy import.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from research.backtest_artifacts import (
    CHART_KINDS, RUN_KEY_FIELDS, RunIdentity, build_identity, chart_filename,
    pnl_filename, rel_path,
)


# --- small io helpers -------------------------------------------------------

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


def _repo_rel(path: Path | str) -> str:
    p = str(path).replace("\\", "/")
    try:
        return rel_path(os.path.relpath(p, os.getcwd()))
    except ValueError:
        return rel_path(p)


# --- PnL timeseries (20-col schema, run_uid keyed) --------------------------

PNL_COLUMNS = [
    "run_uid", "ts", "strategy_name", "symbol", "exchange", "venue_type", "bar_type",
    "sizing_mode", "equity", "pnl", "cumulative_pnl", "drawdown", "drawdown_pct",
    "position", "benchmark_equity", "benchmark_return", "strategy_return",
    "source_equity_curve_path", "source_positions_path", "notes",
]


def pnl_timeseries_rows(equity_rows: list[dict], identity: RunIdentity, *,
                        equity_curve_path: str, positions_path: str) -> list[dict]:
    """Standard PnL series from equity_curve.csv dict rows, tagged with identity.

    ``position`` / ``benchmark_*`` are emitted as ``NA`` when the source column is
    absent or unusable -- never fabricated.
    """
    out: list[dict] = []
    initial = close0 = peak = prev_eq = None
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
        dd = eq - peak
        dd_pct = (dd / peak) if peak else None
        pnl = (eq - prev_eq) if prev_eq is not None else 0.0
        prev_eq = eq
        has_bench = close is not None and close0 is not None
        pos = _f(r.get("position"))
        out.append({
            "run_uid": identity.run_uid,
            "ts": r.get("event_time") or r.get("event_time_ns") or "NA",
            "strategy_name": identity.strategy_name, "symbol": identity.symbol,
            "exchange": identity.exchange, "venue_type": identity.venue_type,
            "bar_type": identity.bar_type, "sizing_mode": identity.sizing_mode,
            "equity": eq, "pnl": pnl, "cumulative_pnl": eq - initial,
            "drawdown": dd, "drawdown_pct": dd_pct if dd_pct is not None else "NA",
            "position": pos if pos is not None else "NA",
            "benchmark_equity": (initial * close / close0) if has_bench else "NA",
            "benchmark_return": (close / close0 - 1.0) if has_bench else "NA",
            "strategy_return": (eq / initial - 1.0) if initial else "NA",
            "source_equity_curve_path": equity_curve_path,
            "source_positions_path": positions_path,
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


# --- charts (matplotlib, guarded) -------------------------------------------

def _chart_x_axis(pnl_rows: list[dict]):
    """Return (x_values, is_time, xlabel). Prefer a readable timestamp axis parsed
    from each row's ``ts`` (ISO 8601); fall back to a bar index if any is unparseable."""
    times = []
    for r in pnl_rows:
        try:
            times.append(datetime.fromisoformat(str(r.get("ts"))))
        except (TypeError, ValueError):
            return list(range(len(pnl_rows))), False, "15m bar index"
    if times:
        return times, True, "time"
    return list(range(len(pnl_rows))), False, "15m bar index"


# Larger, projection-friendly defaults (no seaborn; matplotlib only).
_FIGSIZE = (12.5, 5.2)
_TITLE_FS, _LABEL_FS, _TICK_FS = 15, 13, 11
_STRAT_COLOR, _BENCH_COLOR = "#1f5fae", "#c0504d"


def _style_time_axis(ax, is_time):
    import matplotlib.dates as mdates  # noqa: PLC0415
    if is_time:
        loc = mdates.AutoDateLocator()
        ax.xaxis.set_major_locator(loc)
        ax.xaxis.set_major_formatter(mdates.ConciseDateFormatter(loc))
    ax.tick_params(axis="both", labelsize=_TICK_FS)


def render_charts(pnl_rows: list[dict], run_uid: str, symbol: str,
                  charts_dir: Path) -> dict[str, str | None]:
    """One single-figure PNG per chart kind, named ``<run_uid>_<kind>.png``.

    Titles carry only strategy / symbol / metric (run_uid lives in the filename).
    x-axis is a readable timestamp when ``ts`` parses, else a 15m bar index.
    Returns {kind: path|None}; None means no data OR no matplotlib.
    """
    keys: dict[str, str | None] = {k: None for k in CHART_KINDS}
    if not pnl_rows:
        return keys
    try:
        import matplotlib  # noqa: PLC0415
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt  # noqa: PLC0415
    except Exception:
        return keys
    charts_dir.mkdir(parents=True, exist_ok=True)
    x, is_time, xlabel = _chart_x_axis(pnl_rows)

    def series(col):
        return [(_f(r.get(col)) if r.get(col) not in (None, "NA") else None) for r in pnl_rows]

    def line(col, kind, metric, ylabel, *, pct=False):
        ys = series(col)
        if all(v is None for v in ys):
            return None
        xs = [xi for xi, v in zip(x, ys) if v is not None]
        vs = [(v * 100.0 if pct else v) for v in ys if v is not None]
        fig = plt.figure(figsize=_FIGSIZE)
        ax = fig.add_subplot(111)
        ax.plot(xs, vs, linewidth=1.4, color=_STRAT_COLOR)
        ax.set_title(f"VWM  {symbol}  -  {metric}", fontsize=_TITLE_FS, fontweight="bold")
        ax.set_xlabel(xlabel, fontsize=_LABEL_FS); ax.set_ylabel(ylabel, fontsize=_LABEL_FS)
        ax.grid(True, alpha=0.3)
        _style_time_axis(ax, is_time)
        out = charts_dir / chart_filename(run_uid, kind)
        fig.tight_layout(); fig.savefig(out, dpi=130); plt.close(fig)
        return _repo_rel(out)

    keys["equity_curve"] = line("equity", "equity_curve", "Equity Curve", "equity (USDT)")
    keys["drawdown"] = line("drawdown_pct", "drawdown", "Drawdown", "drawdown (%)", pct=True)
    keys["pnl_curve"] = line("cumulative_pnl", "pnl_curve", "Cumulative PnL", "cumulative PnL (USDT)")
    keys["position"] = line("position", "position", "Position Exposure", "position (contracts)")

    # benchmark_comparison: strategy equity vs buy&hold benchmark equity, same axes
    eq = series("equity"); be = series("benchmark_equity")
    if not all(v is None for v in be) and not all(v is None for v in eq):
        fig = plt.figure(figsize=_FIGSIZE)
        ax = fig.add_subplot(111)
        ax.plot(x, [v if v is not None else float("nan") for v in eq],
                linewidth=1.4, color=_STRAT_COLOR, label="VWM strategy equity")
        ax.plot(x, [v if v is not None else float("nan") for v in be],
                linewidth=1.4, color=_BENCH_COLOR, label="buy & hold benchmark")
        ax.set_title(f"VWM  {symbol}  -  Strategy vs Benchmark", fontsize=_TITLE_FS, fontweight="bold")
        ax.set_xlabel(xlabel, fontsize=_LABEL_FS); ax.set_ylabel("equity (USDT)", fontsize=_LABEL_FS)
        ax.grid(True, alpha=0.3); ax.legend(loc="best", fontsize=11)
        _style_time_axis(ax, is_time)
        out = charts_dir / chart_filename(run_uid, "benchmark_comparison")
        fig.tight_layout(); fig.savefig(out, dpi=130); plt.close(fig)
        keys["benchmark_comparison"] = _repo_rel(out)
    return keys


# --- main eval table (rows=symbol) + run_uid/path columns -------------------

WITH_UID_EXTRA = [
    "run_uid", "pnl_timeseries_path", "pnl_single_path", "chart_dir",
    "equity_curve_chart_path", "drawdown_chart_path", "pnl_chart_path",
    "position_chart_path", "benchmark_chart_path", "raw_run_dir",
    "report_json_path", "trades_path", "fills_path", "run_metadata_path",
    "artifact_status",
]

MANIFEST_COLUMNS = [
    "run_uid", "artifact_type", "artifact_path", "source_data_path",
    "source_run_dir", "status", "created_at", "notes",
]


def _exists_or_na(p: Path) -> str:
    return _repo_rel(p) if p.is_file() else "NA"


def _job_dir_for(backtest_root: Path, summary: dict) -> Path:
    job = summary.get("job_id") or summary.get("output_dir")
    return backtest_root / Path(str(job)).name if job else backtest_root


def run(args) -> dict:
    backtest_root = Path(args.backtest_root)
    deliver = Path(args.deliverable_root)
    for sub in ("pnl", "charts", "tables", "raw_refs"):
        (deliver / sub).mkdir(parents=True, exist_ok=True)
    now_iso = args.now or datetime.now(tz=timezone.utc).isoformat()

    eval_rows = read_csv_rows(Path(args.evaluation_table))
    summaries = json.loads((backtest_root / "summary.json").read_text(encoding="utf-8"))
    if isinstance(summaries, dict):
        summaries = [summaries]
    summ_by_symbol = {str(s.get("symbol", "")).upper(): s for s in summaries}

    pnl_ts_path = deliver / "pnl" / "pnl_timeseries.csv"
    combined_pnl: list[dict] = []
    manifest_rows: list[dict] = []
    with_uid: list[dict] = []
    identities: list[dict] = []
    missing: list[str] = []

    for row in eval_rows:
        symbol = str(row.get("Symbol", "")).upper()
        status = str(row.get("Backtest Status", "NA"))
        s = summ_by_symbol.get(symbol)
        er = dict(row)

        if s is None or status.lower() != "success":
            er.update({c: "NA" for c in WITH_UID_EXTRA})
            er["artifact_status"] = "no_run" if s is None else "failed"
            with_uid.append(er)
            missing.append(symbol)
            continue

        jd = _job_dir_for(backtest_root, s)
        cfg = jd / "config_resolved.yaml"
        identity = build_identity(
            s, strategy=args.strategy, sizing_mode=args.sizing_mode,
            bar_type=args.bar_type, start=args.start, end=args.end,
            strategy_version=args.strategy_version, data_version=args.data_version,
            backtest_engine=args.backtest_engine,
            config_resolved_text=cfg.read_text(encoding="utf-8") if cfg.is_file() else None,
        )
        run_uid = identity.run_uid

        eq_csv = jd / "equity_curve.csv"
        pos_csv = jd / "positions.csv"
        pnl_rows = pnl_timeseries_rows(
            read_csv_rows(eq_csv), identity,
            equity_curve_path=_exists_or_na(eq_csv), positions_path=_exists_or_na(pos_csv))
        single_pnl = deliver / "pnl" / pnl_filename(run_uid)
        write_pnl_csv(pnl_rows, single_pnl)
        combined_pnl.extend(pnl_rows)

        charts = render_charts(pnl_rows, run_uid, symbol, deliver / "charts")
        chart_ok = all(charts.get(k) for k in ("equity_curve", "drawdown", "pnl_curve"))
        artifact_status = "complete" if chart_ok else "partial"

        # eval-table-with-uid row
        er["run_uid"] = run_uid
        er["pnl_timeseries_path"] = _repo_rel(pnl_ts_path)
        er["pnl_single_path"] = _repo_rel(single_pnl)
        er["chart_dir"] = _repo_rel(deliver / "charts")
        er["equity_curve_chart_path"] = charts.get("equity_curve") or "NA"
        er["drawdown_chart_path"] = charts.get("drawdown") or "NA"
        er["pnl_chart_path"] = charts.get("pnl_curve") or "NA"
        er["position_chart_path"] = charts.get("position") or "NA"
        er["benchmark_chart_path"] = charts.get("benchmark_comparison") or "NA"
        er["raw_run_dir"] = _repo_rel(jd)
        er["report_json_path"] = _exists_or_na(jd / "report.json")
        er["trades_path"] = _exists_or_na(jd / "trades.csv")
        er["fills_path"] = _exists_or_na(jd / "fills.csv")
        er["run_metadata_path"] = _exists_or_na(jd / "run_metadata.json")
        er["artifact_status"] = artifact_status
        with_uid.append(er)

        # manifest entries
        def _man(atype, path, src_data, status_):
            manifest_rows.append({
                "run_uid": run_uid, "artifact_type": atype, "artifact_path": path,
                "source_data_path": src_data, "source_run_dir": _repo_rel(jd),
                "status": status_, "created_at": now_iso, "notes": "",
            })

        _man("pnl_timeseries", _repo_rel(single_pnl), _exists_or_na(eq_csv), "ok")
        for kind, atype in (("equity_curve", "equity_curve_chart"),
                            ("drawdown", "drawdown_chart"), ("pnl_curve", "pnl_curve_chart"),
                            ("position", "position_chart"),
                            ("benchmark_comparison", "benchmark_chart")):
            p = charts.get(kind)
            _man(atype, p or "NA", _exists_or_na(eq_csv), "ok" if p else "partial")
        _man("trades", _exists_or_na(jd / "trades.csv"), _exists_or_na(jd / "trades.csv"),
             "ok" if (jd / "trades.csv").is_file() else "missing")
        _man("fills", _exists_or_na(jd / "fills.csv"), _exists_or_na(jd / "fills.csv"),
             "ok" if (jd / "fills.csv").is_file() else "missing")
        _man("report_json", _exists_or_na(jd / "report.json"), _exists_or_na(jd / "report.json"),
             "ok" if (jd / "report.json").is_file() else "missing")
        _man("run_metadata", _exists_or_na(jd / "run_metadata.json"),
             _exists_or_na(jd / "run_metadata.json"),
             "ok" if (jd / "run_metadata.json").is_file() else "missing")

        identities.append({
            "run_uid": run_uid, "artifact_id": identity.artifact_id, "symbol": symbol,
            "params_hash": identity.params_hash, "params_hash_source": identity.params_hash_source,
            "missing_fields": list(identity.missing_fields), "raw_run_dir": _repo_rel(jd),
            "pnl_single_path": _repo_rel(single_pnl), "artifact_status": artifact_status,
        })

    write_pnl_csv(combined_pnl, pnl_ts_path)
    _write_with_uid(with_uid, eval_rows, deliver / "tables" / "batch_evaluation_table_with_uid.csv",
                    deliver / "tables" / "batch_evaluation_table_with_uid.md")
    _write_manifest_csv(manifest_rows, deliver / "tables" / "artifact_manifest.csv")
    _write_manifest_md(manifest_rows, deliver / "tables" / "artifact_manifest.md")
    _write_run_manifest_json(identities, deliver / "manifest.json", args, now_iso, missing)
    _write_dashboard_html(with_uid, identities, deliver, args)
    _write_readme(deliver / "README.md", args, missing)
    _write_boss_summary(deliver / "boss_summary.md", identities, with_uid, args)
    _write_run_paths(identities, deliver / "raw_refs" / "run_paths.md")
    return {"with_uid": with_uid, "identities": identities, "missing": missing,
            "manifest_rows": manifest_rows, "deliver": deliver,
            "pnl_timeseries_path": _repo_rel(pnl_ts_path)}


def _write_with_uid(rows: list[dict], orig: list[dict], csv_path: Path, md_path: Path) -> None:
    base = list(orig[0].keys()) if orig else []
    cols = base + [c for c in WITH_UID_EXTRA if c not in base]
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    with csv_path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        for r in rows:
            w.writerow({c: r.get(c, "NA") for c in cols})
    md_cols = [c for c in ("Symbol", "Total Return", "Excess Return", "Max Drawdown %",
                           "Trade Count", "run_uid", "pnl_single_path",
                           "equity_curve_chart_path", "artifact_status") if c in cols]
    lines = ["| " + " | ".join(md_cols) + " |", "| " + " | ".join("---" for _ in md_cols) + " |"]
    for r in rows:
        lines.append("| " + " | ".join(str(r.get(c, "NA")).replace("|", "\\|") for c in md_cols) + " |")
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_manifest_csv(rows: list[dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=MANIFEST_COLUMNS)
        w.writeheader()
        for r in rows:
            w.writerow({c: r.get(c, "") for c in MANIFEST_COLUMNS})


def _write_manifest_md(rows: list[dict], path: Path) -> None:
    cols = ["run_uid", "artifact_type", "artifact_path", "source_run_dir", "status"]
    lines = ["# Artifact Manifest", "",
             "Every backtest artifact keyed by `run_uid`. Look up a row's `run_uid` from "
             "`batch_evaluation_table_with_uid.csv`, then find all its PnL / chart / raw "
             "files here.", "",
             "| " + " | ".join(cols) + " |", "| " + " | ".join("---" for _ in cols) + " |"]
    for r in rows:
        lines.append("| " + " | ".join(str(r.get(c, "")).replace("|", "\\|") for c in cols) + " |")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_run_manifest_json(identities: list[dict], path: Path, args, now_iso: str,
                             missing: list[str]) -> None:
    payload = {
        "deliverable": "phase1_vwm_crypto_perpetual_2026q2",
        "generated_at": now_iso,
        "strategy": args.strategy, "strategy_version": args.strategy_version,
        "sizing_mode": args.sizing_mode, "bar_type": args.bar_type,
        "window": {"start": args.start, "end": args.end},
        "data_version": args.data_version, "backtest_engine": args.backtest_engine,
        "backtest_root": _repo_rel(args.backtest_root),
        "run_uid_fields": list(RUN_KEY_FIELDS),
        "runs": identities,
        "missing_symbols": missing,
        "note": "no live trading, no private API; reproducible from configs + Binance Vision public data",
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _write_run_paths(identities: list[dict], path: Path) -> None:
    lines = ["# Raw backtest run directories (per run_uid)", ""]
    for r in identities:
        lines.append(f"- **{r['symbol']}** `{r['run_uid']}` -> `{r['raw_run_dir']}`")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


# --- static dashboard (no new dependency) -----------------------------------

def _html_escape(s: str) -> str:
    return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace('"', "&quot;"))


def _deliver_rel(repo_rel_path: str, deliver: Path) -> str:
    """Convert a repo-root-relative path to one relative to the deliverable root
    (so links work when opening dashboard.html locally)."""
    if repo_rel_path in (None, "NA", ""):
        return ""
    try:
        return rel_path(os.path.relpath(repo_rel_path, str(deliver)))
    except ValueError:
        return repo_rel_path


def _write_dashboard_html(with_uid: list[dict], identities: list[dict], deliver: Path, args) -> None:
    succ = [r for r in with_uid if r.get("run_uid", "NA") not in ("NA", None)]
    metric_cols = [c for c in ("Symbol", "Total Return", "Excess Return", "Max Drawdown %",
                               "Sharpe", "Trade Count", "Win Rate", "Profit Factor")
                   if (with_uid and c in with_uid[0])]
    # summary table
    head = "".join(f"<th>{_html_escape(c)}</th>" for c in metric_cols + ["run_uid", "artifact_status"])
    body_rows = ""
    for r in with_uid:
        cells = "".join(f"<td>{_html_escape(r.get(c, 'NA'))}</td>" for c in metric_cols)
        body_rows += (f"<tr data-symbol=\"{_html_escape(r.get('Symbol', ''))}\">{cells}"
                      f"<td>{_html_escape(r.get('run_uid', 'NA'))}</td>"
                      f"<td>{_html_escape(r.get('artifact_status', 'NA'))}</td></tr>")
    # per-symbol panels
    panels = ""
    options = ""
    for r in succ:
        sym = r.get("Symbol", "")
        ruid = r.get("run_uid", "NA")
        options += f'<option value="{_html_escape(sym)}">{_html_escape(sym)} ({_html_escape(ruid)})</option>'
        imgs = ""
        for label, col in (("Equity curve", "equity_curve_chart_path"),
                           ("Benchmark comparison", "benchmark_chart_path"),
                           ("Drawdown", "drawdown_chart_path"),
                           ("Cumulative PnL", "pnl_chart_path"),
                           ("Position", "position_chart_path")):
            src = _deliver_rel(r.get(col, "NA"), deliver)
            if src:
                imgs += (f'<figure><figcaption>{_html_escape(label)}</figcaption>'
                         f'<img loading="lazy" src="{_html_escape(src)}" alt="{_html_escape(label)}"></figure>')
        pnl_link = _deliver_rel(r.get("pnl_single_path", "NA"), deliver)
        run_link = _html_escape(r.get("raw_run_dir", "NA"))
        panels += (
            f'<section class="panel" data-symbol="{_html_escape(sym)}">'
            f'<h2>{_html_escape(sym)} <small>{_html_escape(ruid)}</small></h2>'
            f'<p class="paths">PnL CSV: <a href="{_html_escape(pnl_link)}">{_html_escape(pnl_link)}</a><br>'
            f'Raw run dir: <code>{run_link}</code></p>'
            f'<div class="charts">{imgs}</div></section>')
    html = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Phase 1 VWM Dashboard - {_html_escape(args.sizing_mode)}</title>
<style>
 body{{font-family:-apple-system,Segoe UI,Roboto,Arial,sans-serif;margin:0;padding:24px;color:#1a1a1a;background:#fafafa}}
 h1{{font-size:20px}} h2{{font-size:16px;margin:8px 0}} small{{color:#888;font-weight:normal;font-size:12px}}
 .meta{{color:#555;font-size:13px;margin-bottom:16px}}
 table{{border-collapse:collapse;width:100%;background:#fff;font-size:13px;margin-bottom:20px}}
 th,td{{border:1px solid #e0e0e0;padding:6px 8px;text-align:right}} th{{background:#1f5fae;color:#fff}}
 th:first-child,td:first-child{{text-align:left}}
 .controls{{margin:12px 0}} select{{font-size:14px;padding:4px}}
 .charts{{display:flex;flex-wrap:wrap;gap:16px}} figure{{margin:0;background:#fff;border:1px solid #e0e0e0;padding:8px}}
 figure img{{width:540px;max-width:100%;display:block}} figcaption{{font-size:12px;color:#555;margin-bottom:4px}}
 .panel{{margin:18px 0}} .paths{{font-size:12px;color:#444}} code{{background:#eee;padding:1px 4px}}
 .caveat{{background:#fff6e5;border:1px solid #f0c674;padding:10px;font-size:12px;margin-top:24px}}
</style></head>
<body>
<h1>Phase 1 - VWM Crypto-Perpetual ({_html_escape(args.sizing_mode)})</h1>
<div class="meta">Strategy: <b>{_html_escape(args.strategy)}</b> {_html_escape(args.strategy_version)} |
 Window: {_html_escape(args.start)} ~ {_html_escape(args.end)} | Bar: {_html_escape(args.bar_type)} |
 Engine: {_html_escape(args.backtest_engine)} | Data: {_html_escape(args.data_version)}<br>
 Each row has a unique <b>run_uid</b> linking it to its PnL CSV, charts, and raw run dir.</div>
<h2>Evaluation table</h2>
<table><thead><tr>{head}</tr></thead><tbody>{body_rows}</tbody></table>
<div class="controls">Symbol:
 <select id="sym" onchange="pick(this.value)">
  <option value="__all__">All</option>{options}
 </select></div>
{panels}
<div class="caveat"><b>Caveat:</b> VWM is short-only; funding / margin / liquidation / mark-index are
 not modeled. Offline backtest over Binance Vision <b>public</b> klines. No API key; no account,
 position, or trading endpoints. Screening only, not a performance verdict.</div>
<script>
 function pick(v){{document.querySelectorAll('.panel').forEach(function(p){{
   p.style.display=(v==='__all__'||p.dataset.symbol===v)?'block':'none';}});}}
</script>
</body></html>
"""
    (deliver / "dashboard.html").write_text(html, encoding="utf-8")


def _write_readme(path: Path, args, missing: list[str]) -> None:
    miss = ("Missing (no successful run): " + ", ".join(missing)) if missing else "All symbols traced."
    txt = f"""# Phase 1 Deliverable - VWM Crypto-Perpetual ({args.start} ~ {args.end}, {args.bar_type})

Standardized **result data layer**: every evaluation-table row carries a unique
`run_uid` that pins it to a PnL timeseries, per-run PnL CSV, charts, and the raw
backtest run directory.

## Where things are
- **Main table (with run_uid):** `tables/batch_evaluation_table_with_uid.md` (rows = symbol,
  cols = metrics + `run_uid` + artifact paths). Full columns in the `.csv`.
- **PnL table (combined):** `pnl/pnl_timeseries.csv` (all runs, keyed by `run_uid`).
- **Per-run PnL:** `pnl/<run_uid>_pnl.csv`.
- **Charts:** `charts/<run_uid>_{{equity_curve,drawdown,pnl_curve,position,benchmark_comparison}}.png`.
- **Artifact manifest:** `tables/artifact_manifest.md` / `.csv`.
- **Dashboard:** open `dashboard.html` in a browser (static, no server, no dependency).
- **Run-level manifest:** `manifest.json` (identity fields + which fell back to `unknown`).

## What is a run_uid
A deterministic id built only from experiment-identifying fields:
`<STRATEGY>_<SYMBOL>_<EXCHANGE>_<venue_type>_<bar>_<startYYYYMMDD>_<endYYYYMMDD>_<sizing>_<hash>`
e.g. `VWM_BTCUSDT_BINANCE_futures_um_{args.bar_type}_20260301_20260531_{args.sizing_mode}_xxxxxx`.
The 6-hex suffix hashes the full key (incl. params_hash / data_version / engine), so it is
stable across machines and re-runs and never depends on file timestamps.

## How to trace one table row
1. In `tables/batch_evaluation_table_with_uid.csv`, read the row's `run_uid`.
2. **-> PnL:** `pnl/<run_uid>_pnl.csv` (also concatenated in `pnl/pnl_timeseries.csv`).
3. **-> charts:** the row's `equity_curve_chart_path` / `drawdown_chart_path` /
   `pnl_chart_path` / `position_chart_path` / `benchmark_chart_path`, all under `charts/`.
4. **-> raw source:** the row's `raw_run_dir` (original `equity_curve.csv` / `trades.csv` /
   `fills.csv` / `report.json`). Each chart's `source_data_path` in `artifact_manifest.csv`
   points back to the exact `equity_curve.csv` it was drawn from.

## Dashboard
- Static HTML: open `dashboard.html` (pick a symbol to view its metrics, PnL, and charts).
- Optional Streamlit app (only if streamlit is installed, no install performed here):
  `uv run --no-sync streamlit run apps/phase1_dashboard.py -- --deliverable-root {rel_path(args.deliverable_root)}`

## Caveat
VWM is short-only; funding / margin / liquidation / mark-index are not modeled. Offline
backtest over Binance Vision **public** klines. No API key; no account, position, or trading
endpoints. Screening only, not a performance verdict. {miss}
"""
    path.write_text(txt, encoding="utf-8")


def _write_boss_summary(path: Path, identities: list[dict], with_uid: list[dict], args) -> None:
    traced = len(identities)
    total = len(with_uid)
    txt = f"""# Phase 1 - Boss Summary (run_uid 结果数据层)

- 本阶段在已有多标的评测表基础上，新增了 **run_uid** 和 **artifact manifest**。
- **现在主表每一行都能通过 run_uid 精确对应到 PnL timeseries、单独 PnL CSV、equity curve、
  drawdown chart、PnL curve、position chart、benchmark 对比图和原始 backtest run directory。**
  共 {traced}/{total} 行已完整追溯（成功回测的标的）。
- 这样老板既可以看横向指标，也可以追踪到每个结果背后的原始 PnL 数据和图表。
- 查看方式：直接打开 `dashboard.html`（静态网页，无需服务器），或看
  `tables/batch_evaluation_table_with_uid.md` + `charts/`。
- run_uid 稳定可复现，不依赖文件时间或随机数；同一实验重复生成相同 id。
- 当前结论：VWM 为短仓策略，2026 Q2 多数标的上涨，整体跑输 benchmark；sizing 只改规模不改信号。
- 下一步：双向化 / regime 过滤稳健性 + 永续机制建模；可在 run_uid 数据层上接 Streamlit dashboard。
- Caveat：未建模 funding/保证金/强平/mark；非实盘、无私有 API。

查看顺序：dashboard.html -> tables/batch_evaluation_table_with_uid.md ->
charts/<run_uid>_equity_curve.png -> tables/artifact_manifest.md -> pnl/<run_uid>_pnl.csv
"""
    path.write_text(txt, encoding="utf-8")


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="Build run_uid-anchored PnL artifacts + manifest + dashboard")
    ap.add_argument("--backtest-root", required=True)
    ap.add_argument("--evaluation-table", required=True)
    ap.add_argument("--deliverable-root", required=True)
    ap.add_argument("--strategy", default="VWM")
    ap.add_argument("--strategy-version", default="v1")
    ap.add_argument("--sizing-mode", default="vol_targeted")
    ap.add_argument("--bar-type", default="15m")
    ap.add_argument("--start", required=True)
    ap.add_argument("--end", required=True)
    ap.add_argument("--data-version", default="unknown")
    ap.add_argument("--backtest-engine", default="nautilus_backtest")
    ap.add_argument("--now", default=None, help="ISO timestamp for manifest created_at (default: now)")
    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    res = run(args)
    deliver = res["deliver"]
    print(f"DELIVERABLE_ROOT {deliver}")
    print(f"PNL_TIMESERIES {res['pnl_timeseries_path']}")
    print(f"EVAL_TABLE_WITH_UID {deliver / 'tables' / 'batch_evaluation_table_with_uid.csv'}")
    print(f"ARTIFACT_MANIFEST {deliver / 'tables' / 'artifact_manifest.csv'}")
    print(f"DASHBOARD {deliver / 'dashboard.html'}")
    print(f"RUNS traced={len(res['identities'])} missing={len(res['missing'])} "
          f"manifest_rows={len(res['manifest_rows'])}")
    for r in res["identities"]:
        print(f"  {r['run_uid']}: status={r['artifact_status']} "
              f"params_hash={r['params_hash']}({r['params_hash_source']}) pnl={r['pnl_single_path']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
