"""Local, read-only results viewer — turn a run/batch dir into static HTML.

No server, no network, no re-run. Pull ``outputs/`` back from the compute server,
then::

    python -m results.viewer outputs/backtests/<run>       # single run
    python -m results.viewer outputs/batches/<batch>        # batch (eval table)

It renders charts (if matplotlib is available) and writes ``index.html`` that
embeds the metrics, the report, and the chart PNGs with relative links, so it
opens straight from disk in a browser.
"""
from __future__ import annotations

import argparse
import csv
import json
from html import escape
from pathlib import Path

from results.charts import render_run_charts

_CSS = (
    "body{font-family:system-ui,Arial,sans-serif;margin:24px;color:#1a1a1a}"
    "table{border-collapse:collapse;margin:12px 0}"
    "th,td{border:1px solid #ccc;padding:4px 10px;font-size:14px}"
    "th{background:#f2f2f2;text-align:left}"
    "img{max-width:900px;display:block;margin:8px 0;border:1px solid #eee}"
    "h1{font-size:20px}h2{font-size:16px;margin-top:24px}"
    "code,pre{background:#f7f7f7;padding:2px 4px;border-radius:3px}"
)


def _html_page(title: str, body: str) -> str:
    return (
        f"<!doctype html><html><head><meta charset='utf-8'>"
        f"<title>{escape(title)}</title><style>{_CSS}</style></head>"
        f"<body>{body}</body></html>"
    )


def _metrics_table(metrics: dict) -> str:
    if not metrics:
        return "<p>(no metrics.json)</p>"
    rows = "".join(
        f"<tr><th>{escape(str(k))}</th><td>{escape(str(v))}</td></tr>"
        for k, v in metrics.items()
    )
    return f"<table>{rows}</table>"


def build_run_report(run_dir: str | Path) -> Path:
    """Render charts + write ``index.html`` for a single run directory."""
    run_dir = Path(run_dir)
    charts = render_run_charts(run_dir)
    metrics = _read_json(run_dir / "metrics.json")

    body = [f"<h1>Backtest run: {escape(run_dir.name)}</h1>"]
    body.append("<h2>Metrics</h2>")
    body.append(_metrics_table(metrics))
    if charts:
        body.append("<h2>Charts</h2>")
        for name, rel in charts.items():
            body.append(f"<h3>{escape(name)}</h3><img src='{escape(rel)}' alt='{escape(name)}'>")
    else:
        body.append("<p><em>No charts (matplotlib not installed or no equity_curve.csv).</em></p>")
    report_md = run_dir / "report.md"
    if report_md.is_file():
        body.append("<h2>report.md</h2><pre>")
        body.append(escape(report_md.read_text(encoding="utf-8")))
        body.append("</pre>")

    out = run_dir / "index.html"
    out.write_text(_html_page(f"run {run_dir.name}", "".join(body)), encoding="utf-8")
    return out


def build_batch_report(batch_dir: str | Path) -> Path:
    """Write ``index.html`` for a batch: the eval table + per-run report links."""
    batch_dir = Path(batch_dir)
    table_csv = batch_dir / "evaluation_table.csv"
    rows: list[dict] = []
    if table_csv.is_file():
        with open(table_csv, newline="", encoding="utf-8") as fh:
            rows = list(csv.DictReader(fh))

    # Render each referenced run's report page (best-effort) and link to it.
    for r in rows:
        rd = r.get("output_dir")
        if rd and Path(rd).is_dir():
            try:
                idx = build_run_report(rd)
                r["_link"] = _rel(idx, batch_dir)
            except Exception:  # noqa: BLE001 — a broken run shouldn't sink the batch page
                r["_link"] = ""

    body = [f"<h1>Batch: {escape(batch_dir.name)}</h1>"]
    if rows:
        cols = [c for c in rows[0] if not c.startswith("_")]
        head = "".join(f"<th>{escape(c)}</th>" for c in cols) + "<th>report</th>"
        trs = []
        for r in rows:
            tds = "".join(f"<td>{escape(str(r.get(c, '')))}</td>" for c in cols)
            link = r.get("_link") or ""
            cell = f"<a href='{escape(link)}'>open</a>" if link else "-"
            trs.append(f"<tr>{tds}<td>{cell}</td></tr>")
        body.append(f"<table><tr>{head}</tr>{''.join(trs)}</table>")
    else:
        body.append("<p>(no evaluation_table.csv)</p>")

    out = batch_dir / "index.html"
    out.write_text(_html_page(f"batch {batch_dir.name}", "".join(body)), encoding="utf-8")
    return out


def _rel(path: Path, base: Path) -> str:
    import os

    return os.path.relpath(str(path), str(base)).replace("\\", "/")


def _read_json(path: Path) -> dict:
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Build a static HTML results view")
    parser.add_argument("directory", help="a run dir (has metrics.json) or batch dir (has evaluation_table.csv)")
    args = parser.parse_args(argv)

    d = Path(args.directory)
    if (d / "evaluation_table.csv").is_file():
        out = build_batch_report(d)
    else:
        out = build_run_report(d)
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
