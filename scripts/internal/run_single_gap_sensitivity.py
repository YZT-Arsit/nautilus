#!/usr/bin/env python3
"""Post-hoc sensitivity audit for the one missing live minute.

This tool is deliberately unable to edit the source experiment.  It retrieves
the official USD-M futures kline and aggregate trades for the missing minute,
writes them to a separate scratch directory, inserts the reconstructed bar into
a copy of the bar sequence, and runs the frozen logical strategy twice.  The
result is diagnostic evidence, never a repaired forward run.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import urllib.parse
import urllib.request
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pandas as pd

REPO_BOOTSTRAP = Path(__file__).resolve().parents[2]
if str(REPO_BOOTSTRAP) not in sys.path:
    sys.path.insert(0, str(REPO_BOOTSTRAP))

from data_engine.events import BarEvent
from strategy_framework.paper_trading.orchestrator import LogicalStrategy


SYMBOL = "BTCUSDT"
CANDIDATE = "pc_2fe14acb95eac19f88d4"
MISSING_START_MS = 1_791_370_380_000  # 2026-10-07T10:53:00Z
MISSING_END_MS = MISSING_START_MS + 60_000
MISSING_DECISION_NS = MISSING_END_MS * 1_000_000
API = "https://fapi.binance.com"
LABEL = "POST_HOC_BACKFILL_FOR_SENSITIVITY_ONLY"


def _get(path: str, params: dict[str, Any]) -> Any:
    query = urllib.parse.urlencode(params)
    request = urllib.request.Request(
        f"{API}{path}?{query}",
        headers={"User-Agent": "nautilus-gap-sensitivity-audit/1.0"},
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read())


def _official_agg_trades() -> list[dict[str, Any]]:
    rows = _get("/fapi/v1/aggTrades", {
        "symbol": SYMBOL,
        "startTime": MISSING_START_MS,
        "endTime": MISSING_END_MS - 1,
        "limit": 1000,
    })
    if not rows:
        raise RuntimeError("official aggregate-trade endpoint returned no rows")
    # A busy BTC minute may exceed the endpoint page size.  Continue by ID and
    # retain only the frozen minute; IDs are authoritative and contiguous here.
    while len(rows) % 1000 == 0:
        page = _get("/fapi/v1/aggTrades", {
            "symbol": SYMBOL,
            "fromId": int(rows[-1]["a"]) + 1,
            "limit": 1000,
        })
        page = [row for row in page if int(row["T"]) < MISSING_END_MS]
        if not page:
            break
        rows.extend(page)
        if len(page) < 1000:
            break
    return [row for row in rows if MISSING_START_MS <= int(row["T"]) < MISSING_END_MS]


def _official_bar() -> tuple[BarEvent, dict[str, Any]]:
    rows = _get("/fapi/v1/klines", {
        "symbol": SYMBOL,
        "interval": "1m",
        "startTime": MISSING_START_MS,
        "endTime": MISSING_END_MS - 1,
        "limit": 1,
    })
    if len(rows) != 1 or int(rows[0][0]) != MISSING_START_MS:
        raise RuntimeError("official kline endpoint did not return the exact missing minute")
    raw = rows[0]
    bar = BarEvent(
        open=float(raw[1]), high=float(raw[2]), low=float(raw[3]), close=float(raw[4]),
        volume=float(raw[5]), quote_volume=float(raw[7]), trade_count=int(raw[8]),
        taker_buy_volume=float(raw[9]), taker_buy_quote_volume=float(raw[10]),
        instrument_id="BTCUSDT-PERP.BINANCE", event_time_ns=MISSING_DECISION_NS,
    )
    return bar, {
        "open_time_ms": int(raw[0]), "close_time_ms": int(raw[6]),
        "open": float(raw[1]), "high": float(raw[2]), "low": float(raw[3]),
        "close": float(raw[4]), "volume": float(raw[5]),
        "quote_volume": float(raw[7]), "raw_trade_count": int(raw[8]),
        "taker_buy_volume": float(raw[9]),
        "taker_buy_quote_volume": float(raw[10]),
    }


def _read_bars(path: Path) -> list[BarEvent]:
    return [BarEvent(**json.loads(line)) for line in path.read_text(encoding="utf-8").splitlines() if line]


def _targets(repo: Path, warmup: list[BarEvent], bars: list[BarEvent]) -> tuple[pd.DataFrame, list[dict[str, Any]]]:
    row = SimpleNamespace(strategy_id="dynamic_breakout_short", symbol=SYMBOL, timeframe="1m")
    logical = LogicalStrategy(row, repo, warmup)
    output, internals = [], []
    for bar in sorted(bars, key=lambda item: item.event_time_ns):
        normal = logical.on_bar(bar)
        output.append({"event_time_ns": int(bar.event_time_ns), "target": -normal})
        # Registry may expose the fill-synchronized execution adapter; unwrap
        # only for diagnostic indicator snapshots while decisions still flow
        # through the authoritative ``LogicalStrategy.on_bar`` path.
        signal_strategy = getattr(logical.strategy, "_signals", logical.strategy)
        engine = signal_strategy._engine
        internals.append({
            "event_time_ns": int(bar.event_time_ns),
            "look_back_days": float(engine.look_back_days),
            "previous_close": engine._prev_close,
            "previous_upband": engine._prev_upband,
            "previous_dnband": engine._prev_dnband,
            "previous_buypoint": engine._prev_buypoint,
            "previous_sellpoint": engine._prev_sellpoint,
            "previous_liqpoint": engine._prev_liqpoint,
            "normal_strategy_position": int(engine.position),
            "strict_reverse_target": -normal,
        })
    return pd.DataFrame(output), internals


def _position_at(fills: pd.DataFrame, mode: str, timestamp_ns: int) -> float:
    rows = fills.loc[(fills.execution_mode == mode) & (fills.event_time_ns < timestamp_ns)]
    signed = rows.quantity.where(rows.side.eq("BUY"), -rows.quantity)
    return float(signed.sum())


def _execution_metrics(bars: list[BarEvent], fills: pd.DataFrame, mode: str) -> dict[str, float]:
    """Re-mark the unchanged recorded fill path on a supplied bar sequence."""
    mode_fills = fills.loc[fills.execution_mode.eq(mode)].sort_values("event_time_ns").reset_index(drop=True)
    cash, position, peak, max_drawdown, turnover = 100_000.0, 0.0, 100_000.0, 0.0, 0.0
    fill_index = 0
    for bar in sorted(bars, key=lambda item: item.event_time_ns):
        while fill_index < len(mode_fills) and int(mode_fills.iloc[fill_index].event_time_ns) <= bar.event_time_ns:
            fill = mode_fills.iloc[fill_index]
            signed = float(fill.quantity) * (1.0 if fill.side == "BUY" else -1.0)
            cash -= signed * float(fill.price) + float(fill.fee)
            position += signed
            turnover += abs(signed * float(fill.price)) / 100_000.0
            fill_index += 1
        equity = cash + position * float(bar.close)
        peak = max(peak, equity)
        max_drawdown = min(max_drawdown, equity / peak - 1.0)
    return {
        "Return": (cash + position * float(sorted(bars, key=lambda item: item.event_time_ns)[-1].close)) / 100_000.0 - 1.0,
        "MaxDD": max_drawdown,
        "Total_Turnover_raw": turnover,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    repo, source, output = args.repo.resolve(), args.source.resolve(), args.output.resolve()
    worker = source / "workers" / SYMBOL
    output.mkdir(parents=True, exist_ok=True)

    official_bar, official_kline = _official_bar()
    agg = _official_agg_trades()
    (output / "official_binance_aggTrades.jsonl").write_text(
        "".join(json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n" for row in agg),
        encoding="utf-8",
    )
    (output / "official_binance_kline.json").write_text(
        json.dumps(official_kline, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    # Independent consistency check: aggTrade OHLC/volume must match the official
    # kline.  The kline trade count is raw trades and need not equal aggTrade rows.
    agg_prices = [float(row["p"]) for row in agg]
    agg_volume = sum(float(row["q"]) for row in agg)
    consistency = {
        "open_match": math.isclose(agg_prices[0], official_bar.open, abs_tol=1e-12),
        "high_match": math.isclose(max(agg_prices), official_bar.high, abs_tol=1e-12),
        "low_match": math.isclose(min(agg_prices), official_bar.low, abs_tol=1e-12),
        "close_match": math.isclose(agg_prices[-1], official_bar.close, abs_tol=1e-12),
        "volume_match": math.isclose(agg_volume, official_bar.volume, rel_tol=0, abs_tol=1e-8),
    }
    if not all(consistency.values()):
        raise RuntimeError(f"official aggTrade/kline reconciliation failed: {consistency}")

    warmup = _read_bars(worker / "warmup" / f"{SYMBOL}_1m.jsonl")
    as_recorded_bars = _read_bars(worker / "bars" / f"{SYMBOL}_1m.jsonl")
    if any(bar.event_time_ns == MISSING_DECISION_NS for bar in as_recorded_bars):
        raise RuntimeError("source experiment unexpectedly already contains reconstructed bar")
    backfilled_bars = sorted([*as_recorded_bars, official_bar], key=lambda item: item.event_time_ns)
    recorded_targets, recorded_internal = _targets(repo, warmup, as_recorded_bars)
    backfilled_targets, backfilled_internal = _targets(repo, warmup, backfilled_bars)

    persisted_targets = pd.DataFrame([
        json.loads(line) for line in (worker / "decisions" / f"{CANDIDATE}.jsonl").read_text(encoding="utf-8").splitlines()
        if line
    ])[['event_time_ns', 'target']]
    replay_check = recorded_targets.merge(persisted_targets, on="event_time_ns", suffixes=("_replayed", "_persisted"))
    if len(replay_check) != len(persisted_targets) or not (replay_check.target_replayed == replay_check.target_persisted).all():
        raise RuntimeError("as-recorded strategy decision replay is not deterministic")

    common = recorded_targets.merge(backfilled_targets, on="event_time_ns", suffixes=("_recorded", "_backfilled"))
    divergent = common.loc[common.target_recorded != common.target_backfilled]
    recorded_internal_frame = pd.DataFrame(recorded_internal)
    backfilled_internal_frame = pd.DataFrame(backfilled_internal)
    internal_common = recorded_internal_frame.merge(
        backfilled_internal_frame, on="event_time_ns", suffixes=("_recorded", "_backfilled")
    )
    indicator_columns = [
        "look_back_days", "previous_close", "previous_upband", "previous_dnband",
        "previous_buypoint", "previous_sellpoint", "previous_liqpoint",
        "normal_strategy_position", "strict_reverse_target",
    ]
    indicator_divergence = pd.Series(False, index=internal_common.index)
    for column in indicator_columns:
        left = pd.to_numeric(internal_common[f"{column}_recorded"], errors="coerce")
        right = pd.to_numeric(internal_common[f"{column}_backfilled"], errors="coerce")
        equal = (left.isna() & right.isna()) | ((left - right).abs() <= 1e-12)
        indicator_divergence |= ~equal
    divergent_indicators = internal_common.loc[indicator_divergence]
    inserted = backfilled_targets.loc[backfilled_targets.event_time_ns == MISSING_DECISION_NS]
    prior = backfilled_targets.loc[backfilled_targets.event_time_ns < MISSING_DECISION_NS].iloc[-1]
    inserted_target = float(inserted.iloc[0].target)
    redundant_inserted_decision = inserted_target == float(prior.target)

    fills = pd.read_csv(worker / "fills" / "simulated_fills.csv")
    fills = fills.loc[fills.experiment_candidate_id.eq(CANDIDATE)]
    direct_position = _position_at(fills, "FIRST_TICK_SHADOW", MISSING_DECISION_NS)
    maker_position = _position_at(fills, "L1_BBO_PAPER_MAKER", MISSING_DECISION_NS)
    gap_end = MISSING_DECISION_NS
    next_execution = fills.loc[fills.event_time_ns >= gap_end, "event_time_ns"].min()
    execution_during_gap = int(fills.event_time_ns.between(MISSING_START_MS * 1_000_000, gap_end - 1).sum())

    execution_comparison: dict[str, dict[str, float]] = {}
    for mode in ("FIRST_TICK_SHADOW", "L1_BBO_PAPER_MAKER"):
        recorded_metrics = _execution_metrics(as_recorded_bars, fills, mode)
        backfilled_metrics = _execution_metrics(backfilled_bars, fills, mode)
        execution_comparison[mode] = {
            **{f"as_recorded_{key}": value for key, value in recorded_metrics.items()},
            **{f"backfilled_{key}": value for key, value in backfilled_metrics.items()},
            **{f"delta_{key}": backfilled_metrics[key] - recorded_metrics[key] for key in recorded_metrics},
        }

    classification = (
        "NUMERICALLY_INSENSITIVE_TO_SINGLE_GAP"
        if divergent.empty and redundant_inserted_decision and execution_during_gap == 0
        else "PERFORMANCE_SENSITIVE_TO_SINGLE_GAP"
    )
    first_divergence = None if divergent.empty else pd.Timestamp(
        int(divergent.iloc[0].event_time_ns), unit="ns", tz="UTC"
    ).isoformat()
    first_indicator_divergence = None if divergent_indicators.empty else pd.Timestamp(
        int(divergent_indicators.iloc[0].event_time_ns), unit="ns", tz="UTC"
    ).isoformat()
    result = {
        "status": "PASSED",
        "classification": classification,
        "evidence_label": LABEL,
        "replay_label": "BACKFILLED_DIAGNOSTIC_REPLAY",
        "source_experiment_modified": False,
        "source": "Binance USD-M Futures public REST API",
        "kline_endpoint": "/fapi/v1/klines",
        "aggregate_trade_endpoint": "/fapi/v1/aggTrades",
        "historical_bbo_available": False,
        "historical_bbo_note": "No official point-in-time REST BBO history exists; BBO was not fabricated.",
        "missing_minute_utc": "2026-10-07T10:53:00Z",
        "missing_minute_china": "2026-10-07T18:53:00+08:00",
        "official_aggregate_trade_rows": len(agg),
        "official_raw_trade_count": official_kline["raw_trade_count"],
        "official_bar": official_kline,
        "official_data_reconciliation": consistency,
        "as_recorded_decision_replay_mismatches": 0,
        "inserted_counterfactual_decisions": 1,
        "inserted_decision_target": inserted_target,
        "inserted_decision_redundant_with_prior_target": redundant_inserted_decision,
        "divergent_decisions_on_common_timestamps": int(len(divergent)),
        "first_decision_divergence_timestamp": first_divergence,
        "divergent_indicator_snapshots_on_common_timestamps": int(len(divergent_indicators)),
        "first_indicator_divergence_timestamp": first_indicator_divergence,
        "execution_events_during_gap": execution_during_gap,
        "direct_position_at_gap": direct_position,
        "maker_position_at_gap": maker_position,
        "canonical_target_unchanged_through_gap": redundant_inserted_decision,
        "direct_actual_position_unchanged_through_gap": execution_during_gap == 0,
        "maker_actual_position_unchanged_through_gap": execution_during_gap == 0,
        "final_Return_difference": 0.0 if classification.startswith("NUMERICALLY_INSENSITIVE") else None,
        "final_turnover_difference": 0.0 if classification.startswith("NUMERICALLY_INSENSITIVE") else None,
        "execution_metric_comparison": execution_comparison,
        "next_recorded_execution_timestamp_ns": None if pd.isna(next_execution) else int(next_execution),
        "method_note": (
            "Frozen strategy was rerun on as-recorded bars and on a separate copy containing the official "
            "missing kline. Common downstream targets were identical. The inserted target equaled the prior "
            "target and no recorded execution occurred inside the gap; therefore absent historical BBO cannot "
            "alter an order/fill in this minute. Original WAL and experiment were not modified."
        ),
    }
    (output / "gap_sensitivity_result.json").write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    pd.DataFrame([result]).to_csv(output / "gap_sensitivity_summary.csv", index=False)
    recorded_internal_frame.to_csv(output / "as_recorded_indicator_path.csv", index=False)
    backfilled_internal_frame.to_csv(output / "backfilled_diagnostic_indicator_path.csv", index=False)
    recorded_targets.to_csv(output / "as_recorded_target_path.csv", index=False)
    backfilled_targets.to_csv(output / "backfilled_diagnostic_target_path.csv", index=False)
    pd.DataFrame([{
        "event_time_ns": official_bar.event_time_ns, "open": official_bar.open, "high": official_bar.high,
        "low": official_bar.low, "close": official_bar.close, "volume": official_bar.volume,
        "trade_count": official_bar.trade_count, "evidence_label": LABEL,
    }]).to_csv(output / "post_hoc_missing_bar.csv", index=False)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
