#!/usr/bin/env python3
"""Read-only preflight for the execution-method and strict-reverse review."""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parents[2]
STAGE_A = ROOT / "outputs/deliverables/tick_review_stageA_9symbols"
L1 = ROOT / "outputs/baseline_evaluation/maker_execution_research/l1_pilot"


def partition_dates(path: Path) -> tuple[str, str, int]:
    dates = sorted(
        item.name.split("=", 1)[1]
        for item in path.glob("date=*")
        if item.is_dir() and "=" in item.name
    )
    return (dates[0], dates[-1], len(dates)) if dates else ("", "", 0)


def main() -> None:
    all_results = pd.read_csv(STAGE_A / "all_1m10m15m_results.csv")
    selected = pd.read_csv(STAGE_A / "qualifying_cases.csv")
    l1_availability = pd.read_csv(L1 / "l1_bookticker_availability.csv")
    market_root = (
        ROOT / "historical_data/market_data/asset_class=crypto/exchange=BINANCE"
        / "venue_type=futures_um"
    )
    data_rows = []
    for symbol in sorted(selected.symbol.unique()):
        base = market_root / f"symbol={symbol}"
        row = {"symbol": symbol}
        for label, relative in {
            "bar": "data_type=bar/freq=1m",
            "funding": "data_type=funding_rate/freq=settlement",
            "trade": "data_type=trade/freq=tick",
        }.items():
            first, last, count = partition_dates(base / relative)
            row.update({f"{label}_first": first, f"{label}_last": last, f"{label}_dates": count})
        l1_rows = l1_availability[l1_availability.symbol.eq(symbol)]
        if len(l1_rows):
            available = l1_rows.archive_exists.astype(str).str.lower().isin(["true", "1"])
            row.update(
                l1_first=str(l1_rows.loc[available, "date"].min()) if available.any() else "",
                l1_last=str(l1_rows.loc[available, "date"].max()) if available.any() else "",
                l1_dates=int(available.sum()),
            )
        else:
            row.update(l1_first="", l1_last="", l1_dates=0)
        data_rows.append(row)
    data = pd.DataFrame(data_rows)
    maker_symbols = set(data.loc[data.l1_dates.gt(0), "symbol"])
    maker_selected = selected[selected.symbol.isin(maker_symbols)]
    summary = {
        "stagea_status": json.loads((STAGE_A / "validation_summary.json").read_text())["status"],
        "strategy_ids": int(all_results.strategy_id.nunique()),
        "semantic_groups": int(all_results.semantic_group_id.nunique()),
        "selected_cases": len(selected),
        "selected_strategy_ids": int(selected.strategy_id.nunique()),
        "selected_semantic_groups": int(selected.semantic_group_id.nunique()),
        "selected_by_timeframe": selected.groupby("timeframe").size().to_dict(),
        "selected_by_origin": selected.groupby("source_origin").strategy_id.nunique().to_dict(),
        "maker_l1_symbols": sorted(maker_symbols),
        "maker_pairable_selected_cases": len(maker_selected),
        "maker_pairable_unique_semantic_cases": len(
            maker_selected.drop_duplicates(["semantic_group_id", "symbol", "timeframe"])
        ),
        "negative_normal_selected_cases": int(selected.Return.lt(0).sum()),
        "negative_normal_selected_semantic_cases": len(
            selected[selected.Return.lt(0)].drop_duplicates(["semantic_group_id", "symbol", "timeframe"])
        ),
        "data_coverage": data.to_dict(orient="records"),
    }
    print(json.dumps(summary, indent=2, default=str))


if __name__ == "__main__":
    main()
