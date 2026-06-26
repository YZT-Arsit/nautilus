#!/usr/bin/env python3
"""Optional local Streamlit dashboard for the Phase-1 VWM deliverable.

Read-only over the deliverable root: it never re-runs a backtest, never touches
the network, and needs no trading endpoints. It also requires no extra heavy
dependency beyond Streamlit itself -- tables are read with the stdlib ``csv``
module and charts are the pre-rendered PNGs under ``charts/``.

Run (only if streamlit is already installed; do NOT install it):

    uv run --no-sync streamlit run apps/phase1_dashboard.py -- \
        --deliverable-root outputs/deliverables/phase1_vwm_crypto_perpetual_2026q2

If streamlit is not installed, use the static ``dashboard.html`` in the
deliverable root instead (no dependency required).
"""
from __future__ import annotations

import argparse
import csv
from pathlib import Path


def _read_rows(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    with path.open(encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser()
    ap.add_argument("--deliverable-root",
                    default="outputs/deliverables/phase1_vwm_crypto_perpetual_2026q2")
    # Streamlit passes through args after ``--``; ignore anything unknown.
    args, _ = ap.parse_known_args(argv)
    return args


def main() -> None:
    try:
        import streamlit as st  # noqa: PLC0415
    except Exception as exc:  # pragma: no cover - only hit without streamlit
        raise SystemExit(
            "streamlit is not installed. Open the static dashboard.html instead, "
            "or install streamlit separately (not done by this project).") from exc

    args = _parse_args()
    deliver = Path(args.deliverable_root)
    tables = deliver / "tables"

    st.set_page_config(page_title="Phase 1 VWM Dashboard", layout="wide")
    st.title("Phase 1 - VWM Crypto-Perpetual")
    st.caption("Read-only view of the deliverable. No backtest, no network, no trading "
               "endpoints. Every row is keyed by run_uid.")

    eval_rows = _read_rows(tables / "batch_evaluation_table_with_uid.csv")
    if not eval_rows:
        st.error(f"No evaluation table found under {tables}. Run "
                 "scripts/build_phase1_pnl_artifacts.py first.")
        return

    succ = [r for r in eval_rows if r.get("run_uid", "NA") not in ("NA", "", None)]

    strategies = sorted({r.get("Strategy", "VWM") for r in eval_rows})
    sizings = sorted({r.get("Sizing Method", r.get("sizing_mode", "vol_targeted")) for r in eval_rows})
    symbols = ["All"] + [r.get("Symbol", "") for r in succ]

    c1, c2, c3 = st.columns(3)
    c1.selectbox("Strategy", strategies)
    c3.selectbox("Sizing mode", sizings)
    chosen_symbol = c2.selectbox("Symbol", symbols)

    st.subheader("Evaluation table")
    metric_cols = [c for c in ("Symbol", "Total Return", "Excess Return", "Max Drawdown %",
                               "Sharpe", "Trade Count", "Win Rate", "Profit Factor",
                               "run_uid", "artifact_status") if eval_rows and c in eval_rows[0]]
    st.table([{c: r.get(c, "NA") for c in metric_cols} for r in eval_rows])

    panels = succ if chosen_symbol == "All" else [r for r in succ if r.get("Symbol") == chosen_symbol]
    for r in panels:
        sym, ruid = r.get("Symbol", ""), r.get("run_uid", "NA")
        st.subheader(f"{sym}  -  {ruid}")
        st.write(f"PnL CSV: `{r.get('pnl_single_path', 'NA')}`  |  "
                 f"raw run dir: `{r.get('raw_run_dir', 'NA')}`")
        for label, col in (("Equity curve", "equity_curve_chart_path"),
                           ("Benchmark comparison", "benchmark_chart_path"),
                           ("Drawdown", "drawdown_chart_path"),
                           ("Cumulative PnL", "pnl_chart_path"),
                           ("Position", "position_chart_path")):
            p = r.get(col, "NA")
            if p and p != "NA" and Path(p).is_file():
                st.image(p, caption=f"{sym} {label}", use_column_width=False)

    st.info("Caveat: VWM is short-only; funding / margin / liquidation / mark-index are not "
            "modeled. Screening only, not a performance verdict.")


if __name__ == "__main__":
    main()
