#!/usr/bin/env python3
"""Phase 6E true post-cutoff forward holdout for frozen xlsx_s2_0124."""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import math
import os
import shutil
import urllib.request
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import matplotlib as mpl

mpl.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import yaml

from data_engine.loader import load_events
from results.trade_episode import build_de_risk_episodes
from scripts.internal.build_phase4a_baseline_evaluation import drawdown
from scripts.internal.build_phase4b_cost_episode_audit import exact_be
from scripts.internal.run_all_strategy_timeframe_lag import build_strategy_clock
from scripts.internal.run_all_strategy_timeframe_lag import run_decision_lifecycle
from scripts.internal.run_phase6c_conditional_replication import strategy_hashes
from scripts.internal.run_phase6d_execution_realism import CAPITALS
from scripts.internal.run_phase6d_execution_realism import FEE_PROFILES
from scripts.internal.run_phase6d_execution_realism import HEADLINE_CAPITAL
from scripts.internal.run_phase6d_execution_realism import PRIMARY_FEE
from scripts.internal.run_phase6d_execution_realism import parse_exchange_info
from scripts.internal.run_phase6d_execution_realism import phase6c_root
from scripts.internal.run_phase6d_execution_realism import sha256
from scripts.internal.run_phase6d_execution_realism import simulate_exchange_mechanics


ROOT = Path(__file__).resolve().parents[2]
OUTPUT = ROOT / "outputs/baseline_evaluation/phase6e"
PHASE6D = ROOT / "outputs/baseline_evaluation/phase6d"
DELIVERABLES = ROOT / "outputs/deliverables"
MARKET_ROOT = ROOT / "historical_data/market_data"
STRATEGY = "xlsx_s2_0124"
SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT")
REPLAY_START = "2024-07-01"
HOLDOUT_START = "2026-07-01T00:00:00Z"
DEFAULT_HOLDOUT_END = "2026-08-26T00:00:00Z"
TOL = 1e-10


def atomic_csv(path: Path, frame: pd.DataFrame) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    frame.to_csv(temporary, index=False, encoding="utf-8-sig")
    os.replace(temporary, path)


def atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def digest_files(paths: list[Path]) -> dict[str, Any]:
    files: dict[str, dict[str, Any]] = {}
    for root in paths:
        if not root.exists():
            continue
        candidates = [root] if root.is_file() else sorted(p for p in root.rglob("*") if p.is_file() and "__pycache__" not in p.parts)
        for path in candidates:
            rel = path.relative_to(ROOT).as_posix()
            files[rel] = {"size": path.stat().st_size, "sha256": sha256(path)}
    digest = hashlib.sha256()
    for name, metadata in sorted(files.items()):
        digest.update(f"{name}\0{metadata['size']}\0{metadata['sha256']}\n".encode())
    return {"file_count": len(files), "digest": digest.hexdigest(), "files": files}


def protected_snapshot() -> dict[str, Any]:
    p6c = phase6c_root()
    paths = [
        ROOT / "strategies" / STRATEGY,
        ROOT / "strategies/workbook_parametric",
        ROOT / "configs/semantic_contracts",
        p6c,
        PHASE6D,
        DELIVERABLES / "phase6c_cross_symbol_falsification.zip",
        DELIVERABLES / "phase6d_execution_realism.zip",
    ]
    return digest_files(paths)


def _date_root(symbol: str, data_type: str, frequency: str) -> Path:
    return (
        MARKET_ROOT / "asset_class=crypto" / "exchange=BINANCE"
        / "venue_type=futures_um" / f"symbol={symbol}"
        / f"data_type={data_type}" / f"freq={frequency}"
    )


def partition_inventory(symbol: str, start: pd.Timestamp, end: pd.Timestamp) -> dict[str, Any]:
    bar_root = _date_root(symbol, "bar", "1m")
    funding_root = _date_root(symbol, "funding_rate", "settlement")
    dates = pd.date_range(start.normalize(), end.normalize() - pd.Timedelta(days=1), freq="D", tz="UTC")
    present = []
    rows = 0
    first = last = None
    for date in dates:
        folder = bar_root / f"date={date:%Y-%m-%d}"
        files = sorted(folder.glob("*.parquet"))
        if not files:
            continue
        frame = pd.read_parquet(files, columns=["ts"])
        values = pd.to_datetime(frame.ts, utc=True)
        present.append(f"{date:%Y-%m-%d}")
        rows += len(frame)
        local_first, local_last = values.min(), values.max()
        first = local_first if first is None else min(first, local_first)
        last = local_last if last is None else max(last, local_last)
    funding_rows = 0
    funding_first = funding_last = None
    for folder in sorted(funding_root.glob("date=*")):
        date_text = folder.name.removeprefix("date=")
        if not (f"{start:%Y-%m-%d}" <= date_text < f"{end:%Y-%m-%d}"):
            continue
        files = sorted(folder.glob("*.parquet"))
        if not files:
            continue
        frame = pd.read_parquet(files, columns=["ts"])
        values = pd.to_datetime(frame.ts, utc=True)
        funding_rows += len(frame)
        local_first, local_last = values.min(), values.max()
        funding_first = local_first if funding_first is None else min(funding_first, local_first)
        funding_last = local_last if funding_last is None else max(funding_last, local_last)
    expected_dates = {f"{d:%Y-%m-%d}" for d in dates}
    missing_dates = sorted(expected_dates - set(present))
    return {
        "symbol": symbol,
        "first_available_post_cutoff": None if first is None else first.isoformat(),
        "latest_available_timestamp": None if last is None else last.isoformat(),
        "bar_rows": rows,
        "funding_rows": funding_rows,
        "funding_first": None if funding_first is None else funding_first.isoformat(),
        "funding_last": None if funding_last is None else funding_last.isoformat(),
        "complete_bar_dates": len(present),
        "expected_complete_dates": len(dates),
        "missing_date_count": len(missing_dates),
        "missing_dates": ";".join(missing_dates),
    }


def official_bar_exists(symbol: str, date: pd.Timestamp) -> bool:
    day = f"{date:%Y-%m-%d}"
    url = f"https://data.binance.vision/data/futures/um/daily/klines/{symbol}/1m/{symbol}-1m-{day}.zip"
    request = urllib.request.Request(url, method="HEAD", headers={"User-Agent": "nautilus-phase6e/1"})
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            return int(response.status) == 200
    except Exception:
        return False


def load_range(symbol: str, start: str, end_inclusive: str) -> tuple[list[Any], pd.DataFrame]:
    base = {
        "root": str(MARKET_ROOT), "instrument_id": f"{symbol}-PERP.BINANCE",
        "warmup_bars": 0, "timestamp_column": "ts", "timestamp_unit": "ns",
        "start": start, "end": end_inclusive,
    }
    bar = {**base, "mode": "hive_parquet_bars", "filters": {
        "asset_class": "crypto", "exchange": "BINANCE", "venue_type": "futures_um",
        "symbol": symbol, "data_type": "bar", "freq": "1m",
    }}
    funding_cfg = {**base, "mode": "hive_parquet_funding", "filters": {
        "asset_class": "crypto", "exchange": "BINANCE", "venue_type": "futures_um",
        "symbol": symbol, "data_type": "funding_rate", "freq": "settlement",
    }}
    _, stream = load_events(bar); bars = list(stream)
    _, funding_stream = load_events(funding_cfg)
    funding = pd.DataFrame([{
        "event_time_ns": e.event_time_ns, "mark_price": e.mark_price or 0.0,
        "funding_rate": e.funding_rate,
    } for e in funding_stream])
    return bars, funding


def validate_forward_data(symbol: str, bars: list[Any], funding: pd.DataFrame, start_ns: int, end_ns: int) -> dict[str, Any]:
    times = np.fromiter((b.event_time_ns for b in bars), dtype=np.int64)
    selected = (times >= start_ns) & (times < end_ns)
    forward = times[selected]
    expected = (end_ns - start_ns) // 60_000_000_000
    duplicates = int(len(forward) - len(np.unique(forward)))
    missing = int(expected - len(forward))
    funding_selected = funding[(funding.event_time_ns >= start_ns) & (funding.event_time_ns < end_ns)]
    funding_times = funding_selected.event_time_ns.to_numpy(dtype=np.int64)
    expected_funding = (end_ns - start_ns) // (8 * 3_600_000_000_000)
    funding_duplicates = int(len(funding_times) - len(np.unique(funding_times)))
    funding_missing = int(expected_funding - len(funding_times))
    funding_ordered = bool(len(funding_times) and np.all(np.diff(funding_times) > 0))
    complete = (
        len(forward) == expected and missing == 0 and duplicates == 0
        and len(funding_times) == expected_funding and funding_missing == 0
        and funding_duplicates == 0 and funding_ordered
    )
    return {
        "symbol": symbol, "first_available_post_cutoff": pd.Timestamp(forward[0], unit="ns", tz="UTC").isoformat() if len(forward) else None,
        "latest_available_timestamp": pd.Timestamp(forward[-1], unit="ns", tz="UTC").isoformat() if len(forward) else None,
        "bar_rows": len(forward), "expected_bar_rows": int(expected),
        "funding_rows": len(funding_selected), "expected_funding_rows": int(expected_funding),
        "missing_intervals": missing, "funding_missing_intervals": funding_missing,
        "duplicates": duplicates, "strict_ordering": bool(len(forward) and np.all(np.diff(forward) > 0)),
        "funding_duplicates": funding_duplicates, "funding_strict_ordering": funding_ordered,
        "status": "PASSED" if complete else "FAILED",
    }


def cumulative_drawdown(increments: np.ndarray) -> np.ndarray:
    cumulative = np.cumsum(np.asarray(increments, dtype=float))
    peak = np.maximum.accumulate(np.r_[0.0, cumulative])[1:]
    return cumulative - peak


def continuous_reference(
    direction_pre: float, pre_close: float, direction: np.ndarray,
    opens: np.ndarray, closes: np.ndarray, funding: pd.DataFrame,
    times: np.ndarray, capital: float,
) -> np.ndarray:
    previous_qty = direction_pre * capital / pre_close
    previous_close = pre_close
    result = np.zeros(len(times), dtype=float)
    for i in range(len(times)):
        open_qty = direction[i] * capital / opens[i]
        close_qty = direction[i] * capital / closes[i]
        result[i] = (previous_qty * (opens[i] - previous_close) + open_qty * (closes[i] - opens[i])) / capital
        previous_qty, previous_close = close_qty, closes[i]
    if not funding.empty:
        fts = funding.event_time_ns.to_numpy(np.int64); rates = funding.funding_rate.to_numpy(float)
        held = np.searchsorted(times, fts, side="right") - 1; report = np.searchsorted(times, fts, side="left")
        valid = (held >= 0) & (report < len(times))
        np.add.at(result, report[valid], -direction[held[valid]] * rates[valid])
    return result


def prepare(output: Path, holdout_end: str) -> int:
    if not (PHASE6D / "phase6d_validation_summary.json").is_file():
        raise FileNotFoundError("validated Phase 6D artifacts missing")
    phase6d_validation = json.loads((PHASE6D / "phase6d_validation_summary.json").read_text(encoding="utf-8"))
    if phase6d_validation["status"] != "PHASE6D_PASSED":
        raise ValueError("Phase 6D is not passed")
    candidates = pd.read_csv(PHASE6D / "phase6d_phase6e_candidates.csv")
    if list(candidates.strategy_id) != [STRATEGY]:
        raise ValueError("Phase 6E candidate set is not exactly xlsx_s2_0124")
    start = pd.Timestamp(HOLDOUT_START); end = pd.Timestamp(holdout_end)
    if start.tzinfo is None: start = start.tz_localize("UTC")
    if end.tzinfo is None: end = end.tz_localize("UTC")
    availability = pd.DataFrame([partition_inventory(symbol, start, end) for symbol in SYMBOLS])
    availability.insert(1, "audit_stage", "PRE_ACQUISITION")
    atomic_csv(output / "phase6e_forward_data_availability.csv", availability)
    final_date = end - pd.Timedelta(days=1)
    official = {symbol: official_bar_exists(symbol, final_date) for symbol in SYMBOLS}
    if not all(official.values()):
        raise RuntimeError(f"latest complete official day is not available: {official}")

    p6c = phase6c_root(); freeze_table = pd.read_csv(p6c / "phase6c_candidate_freeze.csv").set_index("representative_strategy_id")
    row = freeze_table.loc[STRATEGY]
    hashes = strategy_hashes(STRATEGY, "STABLE_CLOSE_2BAR_V1;REDUCE_HALF_CURRENT_V1")
    if hashes["canonical_parameter_hash"] != row.canonical_parameter_hash or hashes["strategy_ir_hash"] != row.strategy_ir_hash:
        raise ValueError("strategy parameter/IR hash differs from Phase 6C freeze")
    snapshot = protected_snapshot(); atomic_json(output / "phase6e_protected_hashes_before.json", snapshot)
    freeze = {
        "status": "FROZEN_BEFORE_FORWARD_PERFORMANCE", "freeze_timestamp_utc": datetime.now(UTC).isoformat(),
        "strategy_id": STRATEGY, "strategy_candidate_count": 1,
        "strategy_package_hash": digest_files([ROOT / "strategies" / STRATEGY])["digest"],
        **hashes,
        "phase6d_validation_hash": sha256(PHASE6D / "phase6d_validation_summary.json"),
        "phase6d_execution_master_hash": sha256(PHASE6D / "phase6d_execution_master.csv"),
        "phase6d_instrument_metadata_hash": sha256(PHASE6D / "phase6d_instrument_execution_metadata.csv"),
        "phase6d_fee_schedule_hash": sha256(PHASE6D / "phase6d_fee_schedule_audit.csv"),
        "canonical_timeframe": "1m", "realistic_lag": "lag1m", "premium_mode": "Included",
        "capital": HEADLINE_CAPITAL, "fee_profile": PRIMARY_FEE, "fee_bps": FEE_PROFILES[PRIMARY_FEE],
        "quantity_rounding": "toward_zero", "minQty_enabled": True, "minNotional_enabled": True,
        "slippage_status": "SLIPPAGE_NOT_EMPIRICALLY_MODELLED",
        "symbols": list(SYMBOLS), "holdout_start": start.isoformat(), "holdout_end": end.isoformat(),
        "state_initialization": "historical continuation reconstructed using only pre-cutoff bars",
        "starting_average_entry_policy": "NOT_TRACKED_NET_QUANTITY_MODEL",
        "selection_data_max_timestamp": "<2026-07-01T00:00:00Z",
        "parameter_search": 0, "candidate_reselection": 0, "symbol_reselection": 0,
        "execution_profile_reselection": 0, "semantic_changes": 0,
    }
    atomic_json(output / "phase6e_pre_holdout_freeze.json", freeze)
    window = {
        "status": "FROZEN_BEFORE_FORWARD_PERFORMANCE", "holdout_start": start.isoformat(),
        "holdout_end_exclusive": end.isoformat(), "complete_days": int((end - start).days),
        "latest_common_complete_utc_date": f"{final_date:%Y-%m-%d}",
        "official_daily_archive_available": official,
        "window_policy": "latest common fully complete UTC date available at Phase6E start",
    }
    atomic_json(output / "phase6e_forward_window.json", window)
    print(json.dumps({"status": "READY_FOR_ACQUISITION", "window": window, "preexisting": availability.to_dict("records")}, ensure_ascii=False))
    return 0


def package(output: Path) -> tuple[Path, str, int, int]:
    target = DELIVERABLES / "phase6e_forward_holdout.zip"; temporary = target.with_suffix(".zip.tmp")
    with zipfile.ZipFile(temporary, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for path in sorted(output.rglob("*")):
            if path.is_file() and not path.name.endswith(".tmp") and path.name != "phase6e_delivery.json":
                archive.write(path, Path("phase6e_forward_holdout") / path.relative_to(output))
    os.replace(temporary, target)
    with zipfile.ZipFile(target) as archive:
        bad = archive.testzip(); members = len(archive.infolist())
    if bad: raise RuntimeError(f"ZIP integrity error: {bad}")
    return target, sha256(target), members, target.stat().st_size


def render_figures(output: Path, summary: pd.DataFrame, historical: pd.DataFrame, master: pd.DataFrame, paths: dict[str, pd.DataFrame]) -> None:
    root = output / "figures"; root.mkdir(parents=True, exist_ok=True)
    symbols = list(SYMBOLS); x = np.arange(3)
    for hist_col, fwd_col, ylabel, filename in (
        ("Return", "net_return", "Return (1x)", "01_historical_vs_forward_return.png"),
        ("residual_BE_margin_bps", "residual_BE_margin_bps", "Residual BE margin (bps)", "02_historical_vs_forward_residual_be.png"),
    ):
        h = historical.set_index("symbol").loc[symbols, hist_col].to_numpy(float)
        f = summary.set_index("symbol").loc[symbols, fwd_col].to_numpy(float)
        fig, ax = plt.subplots(figsize=(8, 5)); ax.bar(x-.18, h, .36, label="Pre-cutoff Phase 6D"); ax.bar(x+.18, f, .36, label="Forward holdout"); ax.axhline(0,color="black",lw=.8); ax.set_xticks(x,[s.replace("USDT","") for s in symbols]); ax.set_ylabel(ylabel); ax.legend(); fig.tight_layout(); fig.savefig(root/filename,dpi=160); plt.close(fig)
    fig, ax = plt.subplots(figsize=(8,5)); hist_med = historical.set_index("symbol").loc[symbols,"episode_BE_median"].to_numpy(float); fwd_med=summary.set_index("symbol").loc[symbols,"median_episode_BE"].to_numpy(float); ax.bar(x-.18,hist_med,.36,label="Pre-cutoff"); ax.bar(x+.18,fwd_med,.36,label="Forward"); ax.axhline(0,color="black",lw=.8); ax.set_xticks(x,[s.replace("USDT","") for s in symbols]); ax.set_ylabel("Median episode BE (bps)"); ax.legend(); fig.tight_layout(); fig.savefig(root/"03_historical_vs_forward_episode_be.png",dpi=160); plt.close(fig)
    fig, ax = plt.subplots(figsize=(12,5));
    for symbol, frame in paths.items(): ax.plot(pd.to_datetime(frame.event_time_ns,unit="ns",utc=True),np.cumsum(frame.net_return),label=symbol)
    ax.axhline(0,color="black",lw=.8); ax.set_ylabel("Cumulative Net Return (1x)"); ax.legend(); fig.tight_layout(); fig.savefig(root/"04_forward_cumulative_net_return.png",dpi=160); plt.close(fig)
    fig, ax = plt.subplots(figsize=(12,5));
    for symbol, frame in paths.items(): ax.plot(pd.to_datetime(frame.event_time_ns,unit="ns",utc=True),cumulative_drawdown(frame.net_return.to_numpy(float)),label=symbol)
    ax.set_ylabel("Drawdown (additive 1x)"); ax.legend(); fig.tight_layout(); fig.savefig(root/"05_forward_drawdown.png",dpi=160); plt.close(fig)
    fig, ax = plt.subplots(figsize=(9,6));
    vip0=master[master.fee_profile==PRIMARY_FEE]
    for symbol, child in vip0.groupby("symbol"): ax.plot(child.capital,child.P95_exposure_error,marker="o",label=symbol)
    ax.set_xscale("log"); ax.set_xlabel("Capital (USDT)"); ax.set_ylabel("P95 absolute exposure error"); ax.legend(); fig.tight_layout(); fig.savefig(root/"06_capital_scale_exposure_error.png",dpi=160); plt.close(fig)
    fig, ax = plt.subplots(figsize=(9,6));
    headline=master[master.capital==HEADLINE_CAPITAL]
    for symbol, child in headline.groupby("symbol"): ax.plot(child.effective_fee_bps,child.net_return,marker="o",label=symbol)
    ax.axhline(0,color="black",lw=.8); ax.set_xlabel("Taker fee (bps)"); ax.set_ylabel("Forward Net Return (1x)"); ax.legend(); fig.tight_layout(); fig.savefig(root/"07_fee_profile_sensitivity.png",dpi=160); plt.close(fig)


def execute(output: Path) -> int:  # noqa: C901
    freeze = json.loads((output / "phase6e_pre_holdout_freeze.json").read_text(encoding="utf-8"))
    window = json.loads((output / "phase6e_forward_window.json").read_text(encoding="utf-8"))
    if freeze["status"] != "FROZEN_BEFORE_FORWARD_PERFORMANCE" or window["status"] != "FROZEN_BEFORE_FORWARD_PERFORMANCE":
        raise ValueError("forward window/freeze is not locked")
    start = pd.Timestamp(window["holdout_start"]); end = pd.Timestamp(window["holdout_end_exclusive"])
    start_ns, end_ns = int(start.value), int(end.value); end_date = f"{end - pd.Timedelta(days=1):%Y-%m-%d}"
    rules, _ = parse_exchange_info(PHASE6D / "binance_usdm_exchange_info_snapshot.json")
    source_config = yaml.safe_load((ROOT / "strategies" / STRATEGY / "config.yaml").read_text(encoding="utf-8")) or {}
    current = strategy_hashes(STRATEGY, "STABLE_CLOSE_2BAR_V1;REDUCE_HALF_CURRENT_V1")
    if current["canonical_parameter_hash"] != freeze["canonical_parameter_hash"] or current["strategy_ir_hash"] != freeze["strategy_ir_hash"]:
        raise ValueError("strategy changed after holdout freeze")

    availability_rows=[]; master_rows=[]; mechanics_rows=[]; traces=[]; exceptions=[]; forward_paths={}; regimes=[]; start_states=[]
    physical_signal_runs=0; physical_mechanics_runs=0; max_fee_residual=0.0; max_funding_residual=0.0; illegal=0; min_scored=end_ns
    for symbol in SYMBOLS:
        bars, funding = load_range(symbol, REPLAY_START, end_date)
        availability = validate_forward_data(symbol,bars,funding,start_ns,end_ns); availability["audit_stage"]="FINAL_POST_ACQUISITION"; availability_rows.append(availability)
        if availability["status"] != "PASSED": raise ValueError(f"{symbol} forward data incomplete: {availability}")
        times=np.fromiter((b.event_time_ns for b in bars),dtype=np.int64); opens=np.fromiter((b.open for b in bars),dtype=float); closes=np.fromiter((b.close for b in bars),dtype=float); quote=np.fromiter(((b.quote_volume or 0.0) for b in bars),dtype=float)
        cut=int(np.searchsorted(times,start_ns,side="left")); finish=int(np.searchsorted(times,end_ns,side="left"))
        if cut<=0 or finish!=len(times): raise ValueError(f"{symbol}: replay/forward boundaries invalid")
        strategy_clock=build_strategy_clock(bars,"1m")
        direction,audit,lifecycle=run_decision_lifecycle(strategy_name=STRATEGY,source_config=source_config,frequency="1m",lag_minutes=1,bars_1m=bars,strategy_bars=strategy_clock,end_exclusive_ns=end_ns)
        physical_signal_runs += 1
        direction_forward=direction[cut:finish]; times_f=times[cut:finish]; opens_f=opens[cut:finish]; closes_f=closes[cut:finish]; quote_f=quote[cut:finish]
        funding_f=funding[(funding.event_time_ns>=start_ns)&(funding.event_time_ns<end_ns)].reset_index(drop=True)
        signal_rows=[r for r in audit if r.get("signal_time_ns") is not None and start_ns<=int(r["signal_time_ns"])<end_ns]
        direction_pre=float(direction[cut-1]); days=int((end-start).days)
        start_states.append({"symbol":symbol,"starting_strategy_position":direction_pre,"starting_average_entry":"NOT_TRACKED_NET_QUANTITY_MODEL","starting_strategy_state":"historical continuation reconstructed through cutoff","forward_signal_actions":sum(str(r.get("signal"))!="HOLD" for r in signal_rows),"forward_fill_events":sum(int(r.get("fill_count") or 0) for r in signal_rows)})
        logret=np.diff(np.log(closes_f)); funding_rates=funding_f.funding_rate.to_numpy(float)
        regimes.append({"symbol":symbol,"realized_volatility_1m_std":float(np.std(logret)),"realized_volatility_daily_scaled":float(np.std(logret)*math.sqrt(1440)),"price_trend":float(closes_f[-1]/opens_f[0]-1),"average_bar_quote_volume":float(np.mean(quote_f)),"funding_rate_mean":float(np.mean(funding_rates)),"funding_rate_std":float(np.std(funding_rates))})
        continuous_returns=[]
        for capital in CAPITALS:
            continuous_returns.append(float(continuous_reference(direction_pre,closes[cut-1],direction_forward,opens_f,closes_f,funding_f,times_f,capital).sum()))
            pre_frame,_,_,_=simulate_exchange_mechanics(event_time_ns=times[:cut],direction=direction[:cut],market_open=opens[:cut],close=closes[:cut],quote_volume=quote[:cut],funding=pd.DataFrame(),capital=capital,rule=rules[symbol],trace_limit=0)
            initial_qty=float(pre_frame.executed_quantity.iloc[-1])
            frame,metrics,case_traces,case_exceptions=simulate_exchange_mechanics(event_time_ns=times_f,direction=direction_forward,market_open=opens_f,close=closes_f,quote_volume=quote_f,funding=funding_f,capital=capital,rule=rules[symbol],initial_quantity=initial_qty,previous_close_price=float(closes[cut-1]),trace_limit=5)
            physical_mechanics_runs += 1; min_scored=min(min_scored,int(frame.event_time_ns.min())); illegal += int(metrics["quantity_legality_violations"])
            episodes,episode_summary=build_de_risk_episodes(event_time_ns=frame.event_time_ns,executed_position=np.sign(frame.executed_quantity.to_numpy(float)),turnover_increment=frame.turnover,gross_return_increment=frame.gross_return,strategy=STRATEGY,symbol=symbol,granularity="1m",lag="lag1m",premium_mode="included",variant="original")
            episode_frame=pd.DataFrame(episodes); median_be=float(episode_frame.break_even_bps.median()) if len(episode_frame) else math.nan; positive_be=float((episode_frame.break_even_bps>0).mean()) if len(episode_frame) else math.nan; holding=float(episode_frame.holding_duration_seconds.median()) if len(episode_frame) else math.nan
            mechanics_rows.append({"strategy_id":STRATEGY,"symbol":symbol,"capital":capital,"starting_executed_quantity":initial_qty,"step_size_rejects":metrics["quantity_legality_violations"],"minQty_rejects":metrics["minQty_rejects"],"minNotional_rejects":metrics["minNotional_rejects"],"dust_events":metrics["dust_events"],"mean_exposure_error":metrics["mean_abs_exposure_error"],"P95_exposure_error":metrics["P95_abs_exposure_error"],"max_exposure_error":metrics["max_abs_exposure_error"],"participation_median":metrics["participation_median"],"participation_P95":metrics["participation_P95"],"participation_P99":metrics["participation_P99"],"requested_orders":metrics["requested_order_count"],"executed_orders":metrics["executed_order_count"],"rejected_orders":metrics["rejected_order_count"]})
            for item in case_traces: traces.append({"strategy_id":STRATEGY,"symbol":symbol,"capital":capital,**item})
            for item in case_exceptions: exceptions.append({"strategy_id":STRATEGY,"symbol":symbol,"capital":capital,**item})
            for profile,fee_bps in FEE_PROFILES.items():
                net_inc=frame.gross_return.to_numpy(float)-frame.turnover.to_numpy(float)*fee_bps/10000; fee_return=-float(frame.turnover.sum())*fee_bps/10000; gross=float(frame.gross_return.sum()); turnover=float(frame.turnover.sum()); net=float(net_inc.sum()); gross_be=exact_be(gross,turnover); residual=gross_be-fee_bps
                max_fee_residual=max(max_fee_residual,abs(net-(gross+fee_return))); max_funding_residual=max(max_funding_residual,abs(gross-float(frame.price_return.sum())-float(frame.funding_return.sum())))
                status="FORWARD_MARKET_POSITIVE" if capital==HEADLINE_CAPITAL and profile==PRIMARY_FEE and net>0 and residual>0 else "SENSITIVITY"
                master_rows.append({"strategy_id":STRATEGY,"symbol":symbol,"holdout_start":start.isoformat(),"holdout_end":end.isoformat(),"capital":capital,"fee_profile":profile,"gross_return":gross,"price_return":float(frame.price_return.sum()),"funding_return":float(frame.funding_return.sum()),"fee_return":fee_return,"net_return":net,"MDD":drawdown(net_inc),"turnover":turnover,"gross_BE_bps":gross_be,"effective_fee_bps":fee_bps,"residual_BE_margin_bps":residual,"episode_count":len(episodes),"median_episode_BE":median_be,"positive_episode_BE_fraction":positive_be,"holding_duration_median_seconds":holding,"requested_orders":metrics["requested_order_count"],"executed_orders":metrics["executed_order_count"],"rejected_orders":metrics["rejected_order_count"],"mean_exposure_error":metrics["mean_abs_exposure_error"],"P95_exposure_error":metrics["P95_abs_exposure_error"],"signal_actions":sum(str(r.get("signal"))!="HOLD" for r in signal_rows),"fills":sum(int(r.get("fill_count") or 0) for r in signal_rows),"status":status})
                if capital==HEADLINE_CAPITAL and profile==PRIMARY_FEE:
                    path=frame[["event_time_ns","executed_quantity","executed_exposure","gross_return","turnover"]].copy(); path["net_return"]=net_inc; forward_paths[symbol]=path
        if max(continuous_returns)-min(continuous_returns)>1e-12: raise ValueError(f"{symbol}: continuous capital invariance failed")

    availability=pd.DataFrame(availability_rows); atomic_csv(output/"phase6e_forward_data_availability.csv",availability)
    master=pd.DataFrame(master_rows); mechanics=pd.DataFrame(mechanics_rows); atomic_csv(output/"phase6e_forward_master.csv",master); atomic_csv(output/"phase6e_execution_mechanics.csv",mechanics); atomic_csv(output/"phase6e_representative_order_traces.csv",pd.DataFrame(traces)); atomic_csv(output/"phase6e_execution_exceptions.csv",pd.DataFrame(exceptions) if exceptions else pd.DataFrame(columns=["strategy_id","symbol","capital","exception_type"])); atomic_csv(output/"phase6e_starting_state.csv",pd.DataFrame(start_states)); atomic_csv(output/"phase6e_market_regime.csv",pd.DataFrame(regimes))
    primary=master[(master.capital==HEADLINE_CAPITAL)&(master.fee_profile==PRIMARY_FEE)].copy(); primary["market_status"]=np.where((primary.net_return>0)&(primary.residual_BE_margin_bps>0),"FORWARD_MARKET_POSITIVE","FORWARD_MARKET_FAILED"); atomic_csv(output/"phase6e_forward_summary.csv",primary)
    positives=int((primary.market_status=="FORWARD_MARKET_POSITIVE").sum()); forward_status={3:"FORWARD_REPLICATED_3_OF_3",2:"FORWARD_REPLICATED_2_OF_3",1:"FORWARD_WEAK",0:"FORWARD_FAILED"}[positives]
    p6d_master=pd.read_csv(PHASE6D/"phase6d_execution_master.csv"); historical=p6d_master[(p6d_master.strategy_id==STRATEGY)&(p6d_master.capital==HEADLINE_CAPITAL)&(p6d_master.execution_scenario=="E4_PLUS_EXISTING_REALISTIC_LAG")&(p6d_master.fee_profile==PRIMARY_FEE)].copy()
    p6d_summary=pd.read_csv(PHASE6D/"phase6d_strategy_execution_summary.csv").set_index("strategy_id").loc[STRATEGY]
    inv=[]
    for row in historical.itertuples(index=False):
        prefix=row.symbol.replace("USDT",""); inv.append({"strategy_id":STRATEGY,"symbol":row.symbol,"Return_residual":float(row.Return)-float(p6d_summary[f"{prefix}_net_Return"]),"residual_BE_residual":float(row.residual_BE_margin_bps)-float(p6d_summary[f"{prefix}_residual_BE_margin_bps"])})
    invariance=pd.DataFrame(inv); atomic_csv(output/"phase6e_phase6d_invariance.csv",invariance)
    p6c=pd.read_csv(phase6c_root()/"phase6c_cross_symbol_master.csv"); p6c=p6c[p6c.representative_strategy_id==STRATEGY].set_index("symbol")
    historical["episode_BE_median"] = historical["symbol"].map(p6c["episode_BE_median"])
    days=int((end-start).days); shifts=[]
    for row in primary.itertuples(index=False):
        hist=historical[historical.symbol==row.symbol].iloc[0]; old=p6c.loc[row.symbol]; start_state=next(x for x in start_states if x["symbol"]==row.symbol)
        shifts.append({"strategy_id":STRATEGY,"symbol":row.symbol,"historical_days":729,"forward_days":days,"historical_Return_per_day":hist.Return/729,"forward_Return_per_day":row.net_return/days,"historical_turnover_per_day":hist.executed_turnover/729,"forward_turnover_per_day":row.turnover/days,"historical_signal_actions_per_day":hist.signal_action_count/729,"forward_signal_actions_per_day":start_state["forward_signal_actions"]/days,"historical_episode_count_per_day":old.Episode_Count/729,"forward_episode_count_per_day":row.episode_count/days,"historical_episode_median_BE":old.episode_BE_median,"forward_episode_median_BE":row.median_episode_BE,"historical_positive_BE_fraction":old.episode_BE_positive_fraction,"forward_positive_BE_fraction":row.positive_episode_BE_fraction,"historical_holding_duration_median_seconds":old.holding_duration_median,"forward_holding_duration_median_seconds":row.holding_duration_median_seconds,"historical_funding_contribution":hist.funding_Return,"forward_funding_contribution":row.funding_return})
    shift=pd.DataFrame(shifts); atomic_csv(output/"phase6e_distribution_shift.csv",shift)
    execution_data=pd.DataFrame([{"symbol":s,"bookTicker_available":False,"bid_ask_available":False,"depth_available":False,"primary_model_changed":False,"status":"SLIPPAGE_NOT_EMPIRICALLY_MODELLED"} for s in SYMBOLS]); atomic_csv(output/"phase6e_future_execution_data_availability.csv",execution_data)
    combined_episodes=[]
    for symbol,frame in forward_paths.items():
        episodes,_=build_de_risk_episodes(event_time_ns=frame.event_time_ns,executed_position=np.sign(frame.executed_quantity),turnover_increment=frame.turnover,gross_return_increment=frame.gross_return,strategy=STRATEGY,symbol=symbol,granularity="1m",lag="lag1m",premium_mode="included",variant="original"); combined_episodes.extend(episodes)
    combined_median=float(pd.DataFrame(combined_episodes).break_even_bps.median()) if combined_episodes else math.nan
    evidence_plus=bool(forward_status=="FORWARD_REPLICATED_3_OF_3" and combined_median>0 and len(combined_episodes)>0)
    phase6f="PHASE6F_CANDIDATE" if forward_status=="FORWARD_REPLICATED_3_OF_3" else ("PHASE6F_CANDIDATE" if forward_status=="FORWARD_REPLICATED_2_OF_3" and positives==2 else "NO_FURTHER_AUTOMATIC_RESEARCH")
    decision=pd.DataFrame([{"strategy_id":STRATEGY,"forward_status":forward_status,"forward_evidence_plus":evidence_plus,"positive_markets":positives,"combined_completed_episodes":len(combined_episodes),"combined_episode_median_BE":combined_median,"phase6f_decision":phase6f,"phase6f_started":False,"reason":"frozen 100k VIP0 cross-market forward gate"}]); atomic_csv(output/"phase6e_phase6f_decision.csv",decision)
    render_figures(output,primary,historical,master,forward_paths)
    after=protected_snapshot(); atomic_json(output/"phase6e_protected_hashes_after.json",after); before=json.loads((output/"phase6e_protected_hashes_before.json").read_text(encoding="utf-8")); protected_changes=sorted(k for k in set(before["files"])|set(after["files"]) if before["files"].get(k)!=after["files"].get(k))
    max_inv=float(invariance[["Return_residual","residual_BE_residual"]].abs().to_numpy().max()); validation={"status":"PHASE6E_PASSED" if not protected_changes and max_inv<TOL and min_scored>=start_ns and illegal==0 and max_fee_residual<1e-12 and max_funding_residual<1e-12 else "PHASE6E_FAILED","strategy_groups":1,"markets":3,"holdout_start":start.isoformat(),"holdout_end":end.isoformat(),"complete_days":days,"primary_execution_cases":3,"capital_sensitivity_cases":12,"fee_sensitivity_cases":9,"master_rows":len(master),"logical_role_cases":24,"unique_matrix_cases":36,"physical_signal_runs":physical_signal_runs,"physical_mechanics_runs":physical_mechanics_runs,"minimum_scored_timestamp":pd.Timestamp(min_scored,unit="ns",tz="UTC").isoformat(),"warmup_lookahead_failures":0,"strategy_hash_variants":1,"illegal_quantity_fills":illegal,"max_fee_identity_residual":max_fee_residual,"max_funding_accounting_residual":max_funding_residual,"phase6d_invariance_max_residual":max_inv,"protected_artifact_changes":protected_changes,"forward_status":forward_status,"forward_evidence_plus":evidence_plus,"phase6f_decision":phase6f,"phase6f_started":False,"slippage_status":"SLIPPAGE_NOT_EMPIRICALLY_MODELLED","parameter_optimization":0,"candidate_reselection":0,"symbol_reselection":0,"semantic_changes":0,"production_configs":0,"live_trading":0}
    atomic_json(output/"phase6e_validation_summary.json",validation)
    top=primary.set_index("symbol"); rows="".join(f"<tr><td>{s}</td><td>{top.loc[s,'net_return']:.6f}</td><td>{top.loc[s,'residual_BE_margin_bps']:.3f}</td><td>{int(top.loc[s,'episode_count'])}</td></tr>" for s in SYMBOLS); doc=f"""<!doctype html><meta charset='utf-8'><title>Phase 6E Forward Holdout</title><style>body{{font-family:system-ui;margin:2rem;max-width:1100px}}table{{border-collapse:collapse}}td,th{{border:1px solid #ccc;padding:.4rem}}</style><h1>Phase 6E — True Forward Holdout</h1><p>Strategy: <b>{STRATEGY}</b><br>Research cutoff: 2026-06-30<br>Forward window: [{start.isoformat()}, {end.isoformat()})<br>Status: <b>{forward_status}</b></p><p><b>NO PARAMETER RETUNING · NO STRATEGY RESELECTION · SLIPPAGE_NOT_EMPIRICALLY_MODELLED</b></p><table><tr><th>Market</th><th>100k VIP0 Net Return</th><th>Residual BE bps</th><th>Episodes</th></tr>{rows}</table>{''.join(f"<p><img src='figures/{p.name}' style='max-width:100%'></p>" for p in sorted((output/'figures').glob('*.png')))}"""; temp=output/"phase6e_forward_holdout_review.html.tmp"; temp.write_text(doc,encoding="utf-8"); os.replace(temp,output/"phase6e_forward_holdout_review.html")
    if validation["status"]!="PHASE6E_PASSED": raise RuntimeError(json.dumps(validation,ensure_ascii=False))
    archive,digest,members,size=package(output); delivery={"server_zip":str(archive),"sha256":digest,"member_count":members,"size_bytes":size,"integrity":"PASSED"}; atomic_json(output/"phase6e_delivery.json",delivery); print(json.dumps({**validation,**delivery},ensure_ascii=False)); return 0


def main() -> int:
    parser=argparse.ArgumentParser(description=__doc__); parser.add_argument("--output-root",type=Path,default=OUTPUT); parser.add_argument("--holdout-end",default=DEFAULT_HOLDOUT_END); parser.add_argument("--prepare-only",action="store_true"); args=parser.parse_args(); args.output_root.mkdir(parents=True,exist_ok=True)
    return prepare(args.output_root,args.holdout_end) if args.prepare_only else execute(args.output_root)


if __name__ == "__main__": raise SystemExit(main())
