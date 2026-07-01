#!/usr/bin/env python3
"""Phase 1.5 result presentation & traceability layer (data + index + dashboard).

Reads existing backtest outputs (NEVER re-runs a backtest, no network) and builds
a self-contained, filterable, traceable result system under the deliverable root:

    tables/run_registry.csv                one row per experiment (run_uid keyed)
    tables/evaluation_table_with_uid.csv   full metrics + run_uid + every artifact path
    tables/evaluation_table.csv            core columns for the dashboard
    tables/pnl_timeseries.csv              all runs concatenated
    tables/artifact_manifest.csv           one row per artifact (12 types)
    tables/position_sizing.csv             copied from the backtest root
    tables/sizing_mode_comparison.csv      copied from the comparison dir (if present)
    pnl/<run_uid>_pnl.csv                  per-run PnL series
    charts/<run_uid>_*.png                 equity/drawdown/pnl/position/benchmark
    charts/summary_*_by_symbol.png         cross-run bar charts
    dashboard_data/{filters,metrics_schema,dashboard_index}.json
    dashboard/index.html                   static, no-CDN, filterable dashboard
    manifest.json, README.md               run manifest + minimal usage doc

This module writes NO report / conclusion files; it also archives any pre-existing
report-type markdown (boss_summary.md / *_report.md / conclusion.md) out of the
deliverable root (move, never delete). Pure stdlib for data math; matplotlib (Agg)
only for charts (guarded). No strategy / feature_engine / data_engine import.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from research.backtest_artifacts import (
    ARTIFACT_MANIFEST_COLUMNS, CHART_KINDS, RUN_KEY_FIELDS, build_artifact_record,
    build_identity, discover_run_files, normalize_path, safe_relative_path,
)
from scripts.build_phase1_pnl_artifacts import (
    PNL_COLUMNS, _f, _repo_rel, read_csv_rows, render_charts, pnl_timeseries_rows,
    write_pnl_csv,
)


# --- run_registry -----------------------------------------------------------

RUN_REGISTRY_COLUMNS = [
    "run_uid", "strategy_name", "strategy_version", "symbol", "exchange",
    "venue_type", "contract_type", "bar_type", "start", "end", "sizing_mode",
    "params_hash", "params_hash_source", "data_version", "backtest_engine",
    "raw_run_dir", "status", "created_at", "notes",
]

# eval-table-with-uid: the original metric columns + these traceability columns
WITH_UID_EXTRA = [
    "run_uid", "raw_run_dir", "pnl_timeseries_path", "pnl_single_path", "chart_dir",
    "equity_curve_chart_path", "drawdown_chart_path", "pnl_chart_path",
    "position_chart_path", "benchmark_chart_path", "trades_path", "fills_path",
    "report_json_path", "run_metadata_path", "artifact_status",
]

# core columns surfaced in evaluation_table.csv + the dashboard
CORE_METRIC_COLUMNS = [
    "Symbol", "Total Return", "Excess Return", "Max Drawdown %", "Sharpe",
    "Trade Count", "Win Rate", "Profit Factor", "Order Quantity", "Initial Notional",
]

# (column in eval table, display, unit, type, higher_is_better)
METRIC_SCHEMA = [
    ("Total Return", "Total Return", "ratio", "number", True),
    ("Excess Return", "Excess Return vs Benchmark", "ratio", "number", True),
    ("Max Drawdown %", "Max Drawdown", "ratio", "number", False),
    ("Sharpe", "Sharpe Ratio", "ratio", "number", True),
    ("Profit Factor", "Profit Factor", "ratio", "number", True),
    ("Win Rate", "Win Rate", "ratio", "number", True),
    ("Trade Count", "Trade Count", "count", "integer", None),
]

# (eval column, chart filename stem, neutral title, is_percent)
SUMMARY_CHARTS = [
    ("Total Return", "summary_total_return_by_symbol", "VWM Total Return by Symbol", True),
    ("Excess Return", "summary_excess_return_by_symbol", "VWM Excess Return by Symbol", True),
    ("Max Drawdown %", "summary_max_drawdown_by_symbol", "VWM Max Drawdown by Symbol", True),
    ("Profit Factor", "summary_profit_factor_by_symbol", "VWM Profit Factor by Symbol", False),
]

REPORT_FILE_PATTERNS = ("boss_summary.md", "conclusion.md", "strategy_report.md")


def _exists_or_na(p: Path) -> str:
    return _repo_rel(p) if p.is_file() else "NA"


def _job_dir_for(backtest_root: Path, summary: dict) -> Path:
    job = summary.get("job_id") or summary.get("output_dir")
    return backtest_root / Path(str(job)).name if job else backtest_root


def run(args) -> dict:
    backtest_root = Path(args.backtest_root)
    deliver = Path(args.deliverable_root)
    for sub in ("tables", "pnl", "charts", "dashboard_data", "dashboard"):
        (deliver / sub).mkdir(parents=True, exist_ok=True)
    now_iso = args.now or datetime.now(tz=timezone.utc).isoformat()

    eval_rows = read_csv_rows(Path(args.evaluation_table))
    summaries = json.loads((backtest_root / "summary.json").read_text(encoding="utf-8"))
    if isinstance(summaries, dict):
        summaries = [summaries]
    summ_by_symbol = {str(s.get("symbol", "")).upper(): s for s in summaries}

    pnl_ts_path = deliver / "tables" / "pnl_timeseries.csv"
    combined_pnl: list[dict] = []
    registry: list[dict] = []
    manifest: list[dict] = []
    with_uid: list[dict] = []
    dash_index: dict[str, dict] = {}
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
        files = discover_run_files(jd)
        cfg = files["config_resolved"]
        # bar_type from the authoritative per-job summary (falls back to the CLI
        # default) so the run_uid always reflects the real bar type (e.g. 1m), not
        # whatever --bar-type defaulted to.
        bar_type = str(s.get("bar_type") or args.bar_type)
        identity = build_identity(
            s, strategy=args.strategy, sizing_mode=args.sizing_mode,
            bar_type=bar_type, start=args.start, end=args.end,
            strategy_version=args.strategy_version, data_version=args.data_version,
            backtest_engine=args.backtest_engine,
            config_resolved_text=cfg.read_text(encoding="utf-8") if cfg else None)
        run_uid = identity.run_uid

        eq_csv = files["equity_curve"]
        pos_csv = files["positions"]
        pnl_rows = pnl_timeseries_rows(
            read_csv_rows(eq_csv) if eq_csv else [], identity,
            equity_curve_path=_repo_rel(eq_csv) if eq_csv else "NA",
            positions_path=_repo_rel(pos_csv) if pos_csv else "NA")
        single_pnl = deliver / "pnl" / f"{run_uid}_pnl.csv"
        write_pnl_csv(pnl_rows, single_pnl)
        combined_pnl.extend(pnl_rows)

        charts = render_charts(pnl_rows, run_uid, symbol, deliver / "charts")
        chart_ok = all(charts.get(k) for k in ("equity_curve", "drawdown", "pnl_curve"))
        artifact_status = "complete" if chart_ok else "partial"

        reg_notes = ("missing_fields=" + ",".join(identity.missing_fields)) if identity.missing_fields else ""
        registry.append({
            "run_uid": run_uid, "strategy_name": identity.strategy_name,
            "strategy_version": identity.strategy_version, "symbol": identity.symbol,
            "exchange": identity.exchange, "venue_type": identity.venue_type,
            "contract_type": identity.contract_type, "bar_type": identity.bar_type,
            "start": identity.start, "end": identity.end, "sizing_mode": identity.sizing_mode,
            "params_hash": identity.params_hash, "params_hash_source": identity.params_hash_source,
            "data_version": identity.data_version, "backtest_engine": identity.backtest_engine,
            "raw_run_dir": _repo_rel(jd), "status": "success", "created_at": now_iso,
            "notes": reg_notes,
        })

        chart_paths = {
            "equity_curve_chart_path": charts.get("equity_curve") or "NA",
            "drawdown_chart_path": charts.get("drawdown") or "NA",
            "pnl_chart_path": charts.get("pnl_curve") or "NA",
            "position_chart_path": charts.get("position") or "NA",
            "benchmark_chart_path": charts.get("benchmark_comparison") or "NA",
        }
        er["run_uid"] = run_uid
        er["raw_run_dir"] = _repo_rel(jd)
        er["pnl_timeseries_path"] = _repo_rel(pnl_ts_path)
        er["pnl_single_path"] = _repo_rel(single_pnl)
        er["chart_dir"] = _repo_rel(deliver / "charts")
        er.update(chart_paths)
        er["trades_path"] = _exists_or_na(jd / "trades.csv")
        er["fills_path"] = _exists_or_na(jd / "fills.csv")
        er["report_json_path"] = _exists_or_na(jd / "report.json")
        er["run_metadata_path"] = _exists_or_na(jd / "run_metadata.json")
        er["artifact_status"] = artifact_status
        with_uid.append(er)

        # manifest rows
        def _add(atype, path, src_data, status_):
            manifest.append(build_artifact_record(
                run_uid, atype, path, src_data, _repo_rel(jd), status_, now_iso))

        src_eq = _repo_rel(eq_csv) if eq_csv else "NA"
        _add("pnl_timeseries", _repo_rel(pnl_ts_path), src_eq, "ok")
        _add("pnl_single_csv", _repo_rel(single_pnl), src_eq, "ok")
        for kind, atype in (("equity_curve", "equity_curve_chart"),
                            ("drawdown", "drawdown_chart"), ("pnl_curve", "pnl_curve_chart"),
                            ("position", "position_chart"),
                            ("benchmark_comparison", "benchmark_chart")):
            p = charts.get(kind)
            _add(atype, p or "NA", src_eq, "ok" if p else "partial")
        for atype, fn in (("trades", "trades.csv"), ("fills", "fills.csv"),
                          ("report_json", "report.json"), ("run_metadata", "run_metadata.json")):
            present = (jd / fn).is_file()
            _add(atype, _exists_or_na(jd / fn), _exists_or_na(jd / fn), "ok" if present else "missing")
        _add("raw_run_dir", _repo_rel(jd), _repo_rel(jd), "ok")

        dash_index[run_uid] = {
            "run_uid": run_uid, "symbol": symbol, "sizing_mode": identity.sizing_mode,
            "strategy_name": identity.strategy_name, "bar_type": identity.bar_type,
            "start": identity.start, "end": identity.end, "status": "success",
            "artifact_status": artifact_status,
            "metrics": {c: row.get(c, "NA") for c in CORE_METRIC_COLUMNS if c in row},
            "pnl_single_path": _repo_rel(single_pnl), "pnl_timeseries_path": _repo_rel(pnl_ts_path),
            "chart_paths": chart_paths, "raw_run_dir": _repo_rel(jd),
            "manifest_records": [m for m in manifest if m["run_uid"] == run_uid],
        }

    write_pnl_csv(combined_pnl, pnl_ts_path)
    _write_registry(registry, deliver / "tables" / "run_registry.csv")
    _write_with_uid(with_uid, eval_rows, deliver / "tables" / "evaluation_table_with_uid.csv",
                    deliver / "tables" / "evaluation_table_with_uid.md")
    _write_core_eval(with_uid, deliver / "tables" / "evaluation_table.csv")
    _write_manifest(manifest, deliver / "tables" / "artifact_manifest.csv",
                    deliver / "tables" / "artifact_manifest.md")
    _copy_companions(backtest_root, args.sizing_comparison_dir, deliver / "tables")
    summary_charts = _render_summary_charts(with_uid, deliver / "charts")
    _write_dashboard_data(with_uid, registry, dash_index, summary_charts,
                          deliver / "dashboard_data", args)
    _write_dashboard_html(with_uid, dash_index, summary_charts, deliver, args)
    _write_run_manifest(registry, deliver / "manifest.json", args, now_iso, missing)
    _write_readme(deliver / "README.md", args)
    archived = archive_reports(deliver, Path(args.reports_archive_root), now_iso=now_iso)
    superseded = []
    if getattr(args, "archive_superseded", True):
        sup_root = getattr(args, "superseded_archive_root",
                           "outputs/archive/phase1_deliverable_superseded")
        superseded = archive_superseded_deliverable(deliver, Path(sup_root), now_iso=now_iso)
    return {"with_uid": with_uid, "registry": registry, "manifest": manifest,
            "missing": missing, "deliver": deliver, "archived": archived,
            "superseded": superseded, "summary_charts": summary_charts,
            "pnl_timeseries_path": _repo_rel(pnl_ts_path)}


# --- writers ----------------------------------------------------------------

def _write_csv(rows: list[dict], cols: list[str], path: Path, default: str = "") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        for r in rows:
            w.writerow({c: r.get(c, default) for c in cols})


def _write_md(rows: list[dict], cols: list[str], path: Path) -> None:
    lines = ["| " + " | ".join(cols) + " |", "| " + " | ".join("---" for _ in cols) + " |"]
    for r in rows:
        lines.append("| " + " | ".join(str(r.get(c, "NA")).replace("|", "\\|") for c in cols) + " |")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_registry(rows: list[dict], path: Path) -> None:
    _write_csv(rows, RUN_REGISTRY_COLUMNS, path)


def _write_with_uid(rows: list[dict], orig: list[dict], csv_path: Path, md_path: Path) -> None:
    base = list(orig[0].keys()) if orig else []
    cols = base + [c for c in WITH_UID_EXTRA if c not in base]
    _write_csv(rows, cols, csv_path, default="NA")
    md_cols = [c for c in ("Symbol", "Total Return", "Excess Return", "Max Drawdown %",
                           "run_uid", "pnl_single_path", "equity_curve_chart_path",
                           "artifact_status") if c in cols]
    _write_md(rows, md_cols, md_path)


def _write_core_eval(rows: list[dict], path: Path) -> None:
    cols = [c for c in CORE_METRIC_COLUMNS if any(c in r for r in rows)]
    cols = cols + ["run_uid", "artifact_status"]
    _write_csv(rows, cols, path, default="NA")


def _write_manifest(rows: list[dict], csv_path: Path, md_path: Path) -> None:
    _write_csv(rows, ARTIFACT_MANIFEST_COLUMNS, csv_path)
    md_cols = ["run_uid", "artifact_type", "artifact_path", "source_run_dir", "status"]
    lines = ["# Artifact Manifest", "",
             "One row per artifact, keyed by `run_uid`. Resolve a `run_uid` from "
             "`evaluation_table_with_uid.csv` or `run_registry.csv`.", "",
             "| " + " | ".join(md_cols) + " |", "| " + " | ".join("---" for _ in md_cols) + " |"]
    for r in rows:
        lines.append("| " + " | ".join(str(r.get(c, "")).replace("|", "\\|") for c in md_cols) + " |")
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _copy_companions(backtest_root: Path, sizing_comparison_dir: str | None, tables: Path) -> None:
    ps = backtest_root / "position_sizing.csv"
    if ps.is_file():
        shutil.copy2(ps, tables / "position_sizing.csv")
    if sizing_comparison_dir:
        smc = Path(sizing_comparison_dir) / "sizing_mode_comparison.csv"
        if smc.is_file():
            shutil.copy2(smc, tables / "sizing_mode_comparison.csv")


# --- summary charts (cross-run) ---------------------------------------------

def _render_summary_charts(with_uid: list[dict], charts_dir: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    succ = [r for r in with_uid if r.get("run_uid", "NA") not in ("NA", None)]
    if not succ:
        return out
    try:
        import matplotlib  # noqa: PLC0415
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt  # noqa: PLC0415
    except Exception:
        return out
    charts_dir.mkdir(parents=True, exist_ok=True)
    symbols = [r.get("Symbol", "?") for r in succ]
    for col, stem, title, is_pct in SUMMARY_CHARTS:
        vals = [_f(r.get(col)) for r in succ]
        if all(v is None for v in vals):
            continue
        xs = list(range(len(succ)))
        ys = [((v * 100.0 if is_pct else v) if v is not None else 0.0) for v in vals]
        fig = plt.figure(figsize=(9.5, 5.2))
        ax = fig.add_subplot(111)
        bars = ax.bar(xs, ys, color="#1f5fae", width=0.6)
        ax.set_xticks(xs); ax.set_xticklabels(symbols, rotation=0, fontsize=12)
        ax.set_title(title, fontsize=15, fontweight="bold")
        ax.set_ylabel(col + (" (%)" if is_pct else ""), fontsize=13)
        ax.grid(True, axis="y", alpha=0.3)
        ax.axhline(0, color="#888", linewidth=0.8)
        # value labels above/below each bar
        for b, v in zip(bars, ys):
            ax.annotate(f"{v:.2f}{'%' if is_pct else ''}", (b.get_x() + b.get_width() / 2, v),
                        ha="center", va="bottom" if v >= 0 else "top",
                        fontsize=11, xytext=(0, 3 if v >= 0 else -3), textcoords="offset points")
        out_path = charts_dir / f"{stem}.png"
        fig.tight_layout(); fig.savefig(out_path, dpi=130); plt.close(fig)
        out[stem] = _repo_rel(out_path)
    return out


# --- dashboard data ---------------------------------------------------------

def _uniq(values) -> list:
    seen, out = set(), []
    for v in values:
        if v not in seen and v not in (None, ""):
            seen.add(v); out.append(v)
    return out


def _write_dashboard_data(with_uid: list[dict], registry: list[dict],
                          dash_index: dict, summary_charts: dict, ddir: Path, args) -> None:
    ddir.mkdir(parents=True, exist_ok=True)
    filters = {
        "strategies": _uniq([r.get("Strategy", args.strategy) for r in with_uid]) or [args.strategy],
        "symbols": _uniq([r.get("Symbol", "") for r in with_uid]),
        "sizing_modes": _uniq([r.get("Sizing Method", args.sizing_mode) for r in with_uid]) or [args.sizing_mode],
        "bar_types": _uniq([r.get("Bar Type", args.bar_type) for r in with_uid]) or [args.bar_type],
        "start_dates": _uniq([r.get("Start", args.start) for r in with_uid]) or [args.start],
        "end_dates": _uniq([r.get("End", args.end) for r in with_uid]) or [args.end],
        "status": _uniq([r.get("artifact_status", r.get("Backtest Status", "")) for r in with_uid]),
    }
    (ddir / "filters.json").write_text(json.dumps(filters, indent=2), encoding="utf-8")

    schema = [{"metric_name": c, "display_name": disp, "unit": unit, "type": typ,
               "higher_is_better": hib, "source_table": "evaluation_table_with_uid.csv"}
              for (c, disp, unit, typ, hib) in METRIC_SCHEMA]
    (ddir / "metrics_schema.json").write_text(json.dumps(schema, indent=2), encoding="utf-8")

    index = {"deliverable": "phase1_vwm_crypto_perpetual_2026q2",
             "summary_charts": summary_charts, "runs": dash_index}
    (ddir / "dashboard_index.json").write_text(json.dumps(index, indent=2), encoding="utf-8")


# --- static dashboard (no CDN) ----------------------------------------------

def _esc(s) -> str:
    return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace('"', "&quot;"))


def _dash_rel(repo_rel: str, deliver: Path) -> str:
    if repo_rel in (None, "NA", ""):
        return ""
    return safe_relative_path(repo_rel, str(deliver))


def _write_dashboard_html(with_uid: list[dict], dash_index: dict, summary_charts: dict,
                          deliver: Path, args) -> None:
    succ = [r for r in with_uid if r.get("run_uid", "NA") not in ("NA", None)]
    cols = [c for c in CORE_METRIC_COLUMNS if (with_uid and c in with_uid[0])]
    head = "".join(f"<th>{_esc(c)}</th>" for c in cols + ["sizing", "run_uid", "artifact_status"])
    rows_html = ""
    for r in with_uid:
        tds = "".join(f"<td>{_esc(r.get(c, 'NA'))}</td>" for c in cols)
        rows_html += (f'<tr data-symbol="{_esc(r.get("Symbol",""))}" '
                      f'data-sizing="{_esc(r.get("Sizing Method", args.sizing_mode))}" '
                      f'data-strategy="{_esc(r.get("Strategy", args.strategy))}">{tds}'
                      f'<td>{_esc(r.get("Sizing Method", args.sizing_mode))}</td>'
                      f'<td>{_esc(r.get("run_uid","NA"))}</td>'
                      f'<td>{_esc(r.get("artifact_status","NA"))}</td></tr>')
    sym_opts = "".join(f'<option value="{_esc(s)}">{_esc(s)}</option>'
                       for s in _uniq([r.get("Symbol", "") for r in succ]))
    sizing_opts = "".join(f'<option value="{_esc(s)}">{_esc(s)}</option>'
                          for s in _uniq([r.get("Sizing Method", args.sizing_mode) for r in succ]))
    summ_imgs = "".join(
        f'<figure><figcaption>{_esc(stem)}</figcaption>'
        f'<img loading="lazy" src="{_esc(_dash_rel(p, deliver))}" alt="{_esc(stem)}"></figure>'
        for stem, p in summary_charts.items())
    panels = ""
    for r in succ:
        sym, ruid = r.get("Symbol", ""), r.get("run_uid", "NA")
        imgs = ""
        for label, col in (("Equity curve", "equity_curve_chart_path"),
                           ("Benchmark comparison", "benchmark_chart_path"),
                           ("Drawdown", "drawdown_chart_path"),
                           ("Cumulative PnL", "pnl_chart_path"),
                           ("Position", "position_chart_path")):
            src = _dash_rel(r.get(col, "NA"), deliver)
            if src:
                imgs += (f'<figure><figcaption>{_esc(label)}</figcaption>'
                         f'<img loading="lazy" src="{_esc(src)}" alt="{_esc(label)}"></figure>')
        pnl_link = _dash_rel(r.get("pnl_single_path", "NA"), deliver)
        ts_link = _dash_rel(r.get("pnl_timeseries_path", "NA"), deliver)
        metric_bits = " &nbsp; ".join(
            f"{_esc(c)}: <b>{_esc(r.get(c, 'NA'))}</b>"
            for c in ("Total Return", "Excess Return", "Max Drawdown %", "Sharpe",
                      "Trade Count", "Profit Factor") if c in r)
        panels += (
            f'<section class="panel" data-symbol="{_esc(sym)}" '
            f'data-sizing="{_esc(r.get("Sizing Method", args.sizing_mode))}">'
            f'<h2>{_esc(sym)} <small>run_uid: {_esc(ruid)}</small> '
            f'<span class="status status-{_esc(r.get("artifact_status","NA"))}">'
            f'{_esc(r.get("artifact_status","NA"))}</span></h2>'
            f'<p class="metrics">{metric_bits}</p>'
            f'<table class="pathtbl">'
            f'<tr><td>run_uid</td><td><code>{_esc(ruid)}</code></td></tr>'
            f'<tr><td>PnL CSV</td><td><a href="{_esc(pnl_link)}"><code>{_esc(pnl_link)}</code></a></td></tr>'
            f'<tr><td>PnL timeseries</td><td><a href="{_esc(ts_link)}"><code>{_esc(ts_link)}</code></a></td></tr>'
            f'<tr><td>Raw run dir</td><td><code>{_esc(r.get("raw_run_dir","NA"))}</code></td></tr>'
            f'</table>'
            f'<div class="charts">{imgs}</div></section>')
    html = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Phase 1 Result System - {_esc(args.sizing_mode)}</title>
<style>
 body{{font-family:-apple-system,Segoe UI,Roboto,Arial,sans-serif;margin:0;padding:24px;color:#1a1a1a;background:#fafafa}}
 h1{{font-size:20px}} h2{{font-size:16px;margin:8px 0}} small{{color:#888;font-weight:normal;font-size:12px}}
 .meta{{color:#555;font-size:13px;margin-bottom:16px}}
 table{{border-collapse:collapse;width:100%;background:#fff;font-size:13px;margin-bottom:20px}}
 th,td{{border:1px solid #e0e0e0;padding:6px 8px;text-align:right}} th{{background:#1f5fae;color:#fff}}
 th:first-child,td:first-child{{text-align:left}}
 .controls{{margin:12px 0}} select{{font-size:14px;padding:4px;margin-right:12px}}
 .charts{{display:flex;flex-wrap:wrap;gap:16px}} figure{{margin:0;background:#fff;border:1px solid #e0e0e0;padding:8px}}
 figure img{{width:520px;max-width:100%;display:block}} figcaption{{font-size:12px;color:#555;margin-bottom:4px}}
 .panel{{margin:18px 0;border-top:2px solid #1f5fae;padding-top:8px}} code{{background:#eee;padding:1px 4px}}
 .metrics{{font-size:13px;color:#222;margin:4px 0 8px}}
 .pathtbl{{width:auto;font-size:12px;margin:0 0 10px}} .pathtbl td{{text-align:left;border:1px solid #eee;padding:3px 8px}}
 .pathtbl td:first-child{{color:#666;white-space:nowrap}}
 .status{{font-size:11px;font-weight:normal;padding:1px 8px;border-radius:10px;color:#fff}}
 .status-complete{{background:#2e7d32}} .status-partial{{background:#ef6c00}} .status-NA{{background:#999}}
 .note{{background:#f3f3f3;border:1px solid #ddd;padding:8px;font-size:12px;margin-top:20px}}
</style></head>
<body>
<h1>Phase 1 Result System - VWM Crypto-Perpetual</h1>
<div class="meta">Strategy: <b>{_esc(args.strategy)}</b> {_esc(args.strategy_version)} |
 Window: {_esc(args.start)} ~ {_esc(args.end)} | Bar: {_esc(args.bar_type)} |
 Sizing: {_esc(args.sizing_mode)} | Engine: {_esc(args.backtest_engine)} | Data: {_esc(args.data_version)}<br>
 Each row has a unique <b>run_uid</b> mapping it to its PnL CSV, charts, and raw run dir.</div>
<div class="controls">
 Symbol: <select id="sym" onchange="apply()"><option value="__all__">All</option>{sym_opts}</select>
 Sizing: <select id="siz" onchange="apply()"><option value="__all__">All</option>{sizing_opts}</select>
</div>
<h2>Evaluation table</h2>
<table id="evt"><thead><tr>{head}</tr></thead><tbody>{rows_html}</tbody></table>
<h2>Cross-run summary charts</h2>
<div class="charts">{summ_imgs}</div>
<h2>Per-run detail</h2>
{panels}
<div class="note">Files are local and relative to this page. PnL: <code>pnl/&lt;run_uid&gt;_pnl.csv</code>;
 charts: <code>charts/&lt;run_uid&gt;_*.png</code>; manifest: <code>tables/artifact_manifest.csv</code>.
 Modeling note: funding / margin / liquidation / mark price are not modeled in these backtests.</div>
<script>
 function apply(){{
   var sym=document.getElementById('sym').value, siz=document.getElementById('siz').value;
   function ok(el){{return (sym==='__all__'||el.dataset.symbol===sym)&&(siz==='__all__'||el.dataset.sizing===siz);}}
   document.querySelectorAll('#evt tbody tr').forEach(function(t){{t.style.display=ok(t)?'':'none';}});
   document.querySelectorAll('.panel').forEach(function(p){{p.style.display=ok(p)?'block':'none';}});
 }}
</script>
</body></html>
"""
    (deliver / "dashboard" / "index.html").write_text(html, encoding="utf-8")


def _write_run_manifest(registry: list[dict], path: Path, args, now_iso: str,
                        missing: list[str]) -> None:
    payload = {
        "deliverable": "phase1_vwm_crypto_perpetual_2026q2", "generated_at": now_iso,
        "strategy": args.strategy, "strategy_version": args.strategy_version,
        "sizing_mode": args.sizing_mode, "bar_type": args.bar_type,
        "window": {"start": args.start, "end": args.end},
        "data_version": args.data_version, "backtest_engine": args.backtest_engine,
        "backtest_root": normalize_path(args.backtest_root),
        "run_uid_fields": list(RUN_KEY_FIELDS),
        "runs": [{"run_uid": r["run_uid"], "symbol": r["symbol"], "status": r["status"],
                  "params_hash": r["params_hash"], "params_hash_source": r["params_hash_source"],
                  "raw_run_dir": r["raw_run_dir"]} for r in registry],
        "missing_symbols": missing,
        "note": "no live trading, no private API; offline backtest outputs + Binance Vision public data",
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


# --- minimal README (usage only, no conclusions) ----------------------------

def _write_readme(path: Path, args) -> None:
    txt = f"""# Phase 1 Result System - VWM Crypto-Perpetual

Usage / structure index only. No analysis, no conclusions.

## Directory structure
```
README.md
manifest.json
tables/
  run_registry.csv                one row per experiment (run_uid keyed)
  evaluation_table_with_uid.csv   full metrics + run_uid + artifact paths
  evaluation_table.csv            core columns
  pnl_timeseries.csv              all runs concatenated
  artifact_manifest.csv           one row per artifact
  position_sizing.csv
  sizing_mode_comparison.csv
pnl/<run_uid>_pnl.csv
charts/<run_uid>_{{equity_curve,drawdown,pnl_curve,position,benchmark_comparison}}.png
charts/summary_*_by_symbol.png
dashboard_data/{{filters,metrics_schema,dashboard_index}}.json
dashboard/index.html
```

## run_uid
`<STRATEGY>_<SYMBOL>_<EXCHANGE>_<venue>_<bar>_<startYYYYMMDD>_<endYYYYMMDD>_<sizing>_<hash>`.
Deterministic, stable, independent of file timestamps. Built from: strategy_name,
strategy_version, symbol, exchange, venue_type, contract_type, bar_type, start, end,
sizing_mode, params_hash, data_version, backtest_engine. Missing fields become `unknown`
and are flagged in `tables/run_registry.csv` notes.

## Main table
`tables/evaluation_table_with_uid.csv` (rows = symbol). Core view: `tables/evaluation_table.csv`.

## From one table row to its PnL
Read the row's `run_uid`, then open `pnl/<run_uid>_pnl.csv` (also in `tables/pnl_timeseries.csv`).

## From one table row to its charts
The row's `equity_curve_chart_path` / `drawdown_chart_path` / `pnl_chart_path` /
`position_chart_path` / `benchmark_chart_path` point under `charts/`.

## From a chart back to raw data
The row's `raw_run_dir` holds the original `equity_curve.csv` / `trades.csv` / `fills.csv` /
`report.json`. In `tables/artifact_manifest.csv`, each artifact's `source_data_path` names the
exact file it was derived from.

## Dashboard
- Static: open `dashboard/index.html` in a browser (no server, no network). Filter by symbol / sizing.
- Optional Streamlit (only if already installed; not installed by this project):
  `uv run --no-sync streamlit run apps/phase1_dashboard.py -- --deliverable-root {normalize_path(args.deliverable_root)}`

## Modeling note
Funding / margin / liquidation / mark price are not modeled in these backtests.
"""
    path.write_text(txt, encoding="utf-8")


# --- Step 10: archive report-type files (move, never delete) ----------------

def _is_report_file(name: str) -> bool:
    low = name.lower()
    if low == "readme.md":
        return False
    if low in REPORT_FILE_PATTERNS:
        return True
    return low.endswith("_report.md") or low.endswith("_audit.md")


def archive_reports(deliver: Path, archive_root: Path, *, now_iso: str) -> list[dict]:
    """Move any report/conclusion markdown out of the deliverable root (top level
    only; pure table .md under tables/ is kept). Never deletes. Writes a manifest."""
    moved: list[dict] = []
    if not deliver.is_dir():
        return moved
    candidates = [p for p in deliver.iterdir() if p.is_file() and _is_report_file(p.name)]
    if not candidates:
        return moved
    archive_root.mkdir(parents=True, exist_ok=True)
    for src in candidates:
        dst = archive_root / src.name
        note = ""
        if dst.exists():
            note = "destination exists, skipped (no overwrite)"
        else:
            shutil.move(str(src), str(dst))
        moved.append({"old_path": normalize_path(src), "new_path": normalize_path(dst),
                      "reason": "report/conclusion file removed from deliverable root",
                      "moved_at": now_iso, "reversible": "yes",
                      "moved": "no" if note else "yes", "notes": note})
    _write_archive_manifest(moved, archive_root / "archive_manifest.csv")
    return moved


_ARCHIVE_MANIFEST_COLS = ["old_path", "new_path", "reason", "moved_at", "reversible", "moved", "notes"]


def _write_archive_manifest(moved: list[dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=_ARCHIVE_MANIFEST_COLS)
        w.writeheader()
        for r in moved:
            w.writerow(r)


# files superseded by the Phase-1.5+ structure (relative to the deliverable root)
SUPERSEDED_DELIVERABLE_FILES = [
    "dashboard.html",                              # -> dashboard/index.html
    "tables/artifact_index.csv",                   # -> tables/artifact_manifest.csv
    "tables/artifact_index.md",
    "tables/batch_evaluation_table.csv",           # -> tables/evaluation_table*.csv
    "tables/batch_evaluation_table.md",
    "tables/batch_evaluation_table_with_uid.csv",  # -> tables/evaluation_table_with_uid.csv
    "tables/batch_evaluation_table_with_uid.md",
    "raw_refs/run_paths.md",                       # -> tables/run_registry.csv
]


def _superseded_pnl_files(deliver: Path) -> list[Path]:
    """Old per-symbol PnL CSVs (e.g. ``BTCUSDT_pnl.csv``) superseded by run_uid-named
    ones. run_uid files contain ``_BINANCE_`` in the stem; legacy ones do not."""
    pnl = deliver / "pnl"
    if not pnl.is_dir():
        return []
    return [p for p in sorted(pnl.iterdir())
            if p.is_file() and p.name.endswith("_pnl.csv") and "_BINANCE_" not in p.name]


def _superseded_chart_files(deliver: Path) -> list[Path]:
    """Old per-symbol charts (e.g. ``BTCUSDT_equity_curve.png``) superseded by
    run_uid-named ones. Keep run_uid charts (contain ``_BINANCE_``) and cross-run
    ``summary_*`` charts; everything else under charts/ is legacy."""
    cdir = deliver / "charts"
    if not cdir.is_dir():
        return []
    return [p for p in sorted(cdir.iterdir())
            if p.is_file() and p.suffix == ".png"
            and "_BINANCE_" not in p.name and not p.name.startswith("summary_")]


def archive_superseded_deliverable(deliver: Path, archive_root: Path, *, now_iso: str) -> list[dict]:
    """Move known-superseded deliverable files (old dashboard.html / artifact_index /
    batch_evaluation_table* / legacy per-symbol PnL / raw_refs) into an archive root,
    preserving their relative subpath. MOVE only, never delete; dst-exists is skipped.
    Only files in the explicit superseded set are touched -- unknowns stay (manual_review).
    ``dashboard.html`` is archived only once ``dashboard/index.html`` exists.
    """
    moved: list[dict] = []
    if not deliver.is_dir():
        return moved
    targets = [deliver / rel for rel in SUPERSEDED_DELIVERABLE_FILES]
    targets += _superseded_pnl_files(deliver)
    targets += _superseded_chart_files(deliver)
    for src in targets:
        if not src.is_file():
            continue
        if src.name == "dashboard.html" and not (deliver / "dashboard" / "index.html").is_file():
            continue                                   # keep until the replacement exists
        rel = src.relative_to(deliver)
        dst = archive_root / rel
        note = ""
        if dst.exists():
            note = "destination exists, skipped (no overwrite)"
        else:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(src), str(dst))
        moved.append({"old_path": normalize_path(src), "new_path": normalize_path(dst),
                      "reason": "superseded by Phase-1.5+ result structure",
                      "moved_at": now_iso, "reversible": "yes",
                      "moved": "no" if note else "yes", "notes": note})
    # drop now-empty legacy dirs (reversible: regenerated on demand; no data loss)
    raw_refs = deliver / "raw_refs"
    if raw_refs.is_dir() and not any(raw_refs.iterdir()):
        raw_refs.rmdir()
    if moved:
        _write_archive_manifest(moved, archive_root / "archive_manifest.csv")
    return moved


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="Build the Phase-1 result presentation/traceability layer")
    ap.add_argument("--backtest-root", required=True)
    ap.add_argument("--evaluation-table", required=True)
    ap.add_argument("--deliverable-root", required=True)
    ap.add_argument("--strategy", default="VWM")
    ap.add_argument("--strategy-version", default="v1")
    ap.add_argument("--sizing-mode", default="vol_targeted")
    ap.add_argument("--bar-type", default="15m")
    ap.add_argument("--start", required=True)
    ap.add_argument("--end", required=True)
    ap.add_argument("--data-version", default="binance_vision_2026q2")
    ap.add_argument("--backtest-engine", default="nautilus_backtest")
    ap.add_argument("--sizing-comparison-dir",
                    default="outputs/backtests/vwm_crypto_perpetual_2026q2_sizing_comparison")
    ap.add_argument("--reports-archive-root", default="outputs/archive/phase1_reports_removed")
    ap.add_argument("--superseded-archive-root", default="outputs/archive/phase1_deliverable_superseded")
    ap.add_argument("--archive-superseded", dest="archive_superseded", action="store_true", default=True,
                    help="move superseded legacy deliverable files into the archive (default on)")
    ap.add_argument("--no-archive-superseded", dest="archive_superseded", action="store_false")
    ap.add_argument("--now", default=None, help="ISO timestamp for created_at (default: now)")
    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    res = run(args)
    deliver = res["deliver"]
    print(f"DELIVERABLE_ROOT {deliver}")
    print(f"RUN_REGISTRY {deliver / 'tables' / 'run_registry.csv'}")
    print(f"EVAL_WITH_UID {deliver / 'tables' / 'evaluation_table_with_uid.csv'}")
    print(f"EVAL_CORE {deliver / 'tables' / 'evaluation_table.csv'}")
    print(f"PNL_TIMESERIES {res['pnl_timeseries_path']}")
    print(f"ARTIFACT_MANIFEST {deliver / 'tables' / 'artifact_manifest.csv'}")
    print(f"DASHBOARD {deliver / 'dashboard' / 'index.html'}")
    print(f"RUNS traced={len(res['registry'])} missing={len(res['missing'])} "
          f"manifest_rows={len(res['manifest'])} summary_charts={len(res['summary_charts'])}")
    print(f"REPORTS_ARCHIVED {len(res['archived'])} "
          + " ".join(Path(a['old_path']).name for a in res['archived']))
    sup = res.get("superseded", [])
    print(f"SUPERSEDED_ARCHIVED {sum(1 for s in sup if s['moved'] == 'yes')} "
          + " ".join(Path(s['old_path']).name for s in sup if s['moved'] == 'yes'))
    for r in res["registry"]:
        print(f"  {r['run_uid']}: params_hash={r['params_hash']}({r['params_hash_source']}) "
              f"raw_run_dir={r['raw_run_dir']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
