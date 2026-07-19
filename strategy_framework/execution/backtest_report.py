"""Dependency-free backtest analytics + artifact writer.

Turns the raw streams a backtest produces - bar *marks*, *signals*, *intents*,
and **fills** - into an equity curve, a trade list, metrics, and a directory of
CSV/JSON/Markdown artifacts under ``outputs/backtests/<run_name>/``.

This is **not** a matching engine. Fills are produced *upstream* - either by the
dependency-free :class:`IntentFillSimulator` (``mode="simulated"``) or by a native
Nautilus ``BacktestEngine`` (``mode="nautilus_native"``). This module only does
mark-to-market accounting and reporting on top of those fills, so both execution
modes share exactly one report shape.

No Nautilus, no pandas - stdlib ``csv`` / ``json`` only (``yaml`` is used opportun-
istically for ``config.yaml`` and degrades to ``config.json`` when unavailable).
"""
from __future__ import annotations

import csv
import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from strategy_framework.execution.reports import FillRecord

_ACTIONABLE = ("BUY", "SELL")


def _ns_to_iso(ts_ns: int | None) -> str | None:
    if ts_ns is None:
        return None
    try:
        return datetime.fromtimestamp(ts_ns / 1e9, tz=timezone.utc).isoformat()
    except (OverflowError, OSError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Accounting state (single instrument tracked independently; multi-instrument
# safe because every record carries its instrument_id).
# ---------------------------------------------------------------------------

@dataclass
class _Pos:
    qty: float = 0.0
    avg: float = 0.0
    realized: float = 0.0
    last_price: float = 0.0
    entry_time_ns: int | None = None


@dataclass
class TradeRow:
    instrument_id: str
    side: str  # LONG / SHORT (the direction that was opened and closed)
    quantity: float
    entry_time_ns: int | None
    exit_time_ns: int | None
    entry_price: float
    exit_price: float
    realized_pnl: float
    win: bool


@dataclass
class BacktestResult:
    """Everything written to disk plus the in-memory metrics (for tests)."""

    run_name: str
    output_dir: Path
    metrics: dict[str, Any]
    files: dict[str, str]
    trades: list[TradeRow] = field(default_factory=list)
    equity_curve: list[dict[str, Any]] = field(default_factory=list)
    final_positions: list[dict[str, Any]] = field(default_factory=list)


class _Accountant:
    """Replays fills into cash / positions / realized-PnL and emits round-trip trades."""

    def __init__(self, initial_cash: float, fee_rate: float = 0.0) -> None:
        self.cash = float(initial_cash)
        self.fee_rate = float(fee_rate)
        self._pos: dict[str, _Pos] = {}
        self.trades: list[TradeRow] = []
        self.total_commission = 0.0  # sum of all fill commissions (charged once)
        self.total_funding = 0.0

    def position(self, instrument_id: str) -> _Pos:
        return self._pos.setdefault(instrument_id, _Pos())

    def apply_fill(self, fill: FillRecord) -> None:
        pos = self.position(fill.instrument_id)
        qty = abs(float(fill.quantity))
        if qty <= 0:
            return
        price = float(fill.price)
        signed = qty if fill.side == "BUY" else -qty
        # Commission: prefer an explicit value from the engine, else fee_rate model.
        commission = float((fill.metadata or {}).get("commission") or 0.0)
        if commission == 0.0 and self.fee_rate:
            commission = qty * price * self.fee_rate
        self.total_commission += commission

        notional = qty * price
        self.cash += (-notional if fill.side == "BUY" else notional) - commission

        c, a = pos.qty, pos.avg
        if c == 0 or (c > 0) == (signed > 0):
            # opening or increasing in the same direction
            if c == 0:
                pos.entry_time_ns = fill.event_time_ns
            new_qty = c + signed
            pos.avg = (a * abs(c) + price * abs(signed)) / abs(new_qty)
            pos.qty = new_qty
        else:
            # reducing / closing (possibly flipping)
            closing = min(abs(signed), abs(c))
            sign_c = 1.0 if c > 0 else -1.0
            realized = closing * (price - a) * sign_c
            pos.realized += realized
            new_qty = c + signed
            # ``win`` is judged on the *reported* (rounded) gross realized PnL so
            # the flag is always consistent with the trades.csv ``realized_pnl``
            # column. Reversal/partial-fill fragments can close at ~the entry
            # price, leaving a floating residual that rounds to 0 -> not a win.
            self.trades.append(
                TradeRow(
                    instrument_id=fill.instrument_id,
                    side="LONG" if c > 0 else "SHORT",
                    quantity=closing,
                    entry_time_ns=pos.entry_time_ns,
                    exit_time_ns=fill.event_time_ns,
                    entry_price=a,
                    exit_price=price,
                    realized_pnl=realized,
                    win=round(realized, 8) > 0,
                )
            )
            if abs(signed) > abs(c):  # flipped past flat -> new position
                pos.avg = price
                pos.qty = new_qty
                pos.entry_time_ns = fill.event_time_ns
            elif new_qty == 0:
                pos.avg = 0.0
                pos.qty = 0.0
                pos.entry_time_ns = None
            else:
                pos.qty = new_qty  # partial close, avg unchanged
        pos.last_price = price

    def mark(self, instrument_id: str, price: float) -> None:
        if instrument_id in self._pos:
            self._pos[instrument_id].last_price = price

    def apply_funding(self, event: Any, fallback_mark: float) -> float:
        """Settle one funding cashflow; positive means cash received."""
        pos = self.position(event.instrument_id)
        mark = event.mark_price if event.mark_price is not None else fallback_mark
        payment = -pos.qty * float(mark) * float(event.funding_rate)
        self.cash += payment
        self.total_funding += payment
        return payment

    def equity(self, marks: dict[str, float]) -> float:
        eq = self.cash
        for iid, pos in self._pos.items():
            price = marks.get(iid, pos.last_price)
            eq += pos.qty * price
        return eq

    def unrealized(self, marks: dict[str, float]) -> float:
        total = 0.0
        for iid, pos in self._pos.items():
            price = marks.get(iid, pos.last_price)
            total += pos.qty * (price - pos.avg)
        return total

    def realized_total(self) -> float:
        return sum(p.realized for p in self._pos.values())


def _max_drawdown(equity: list[float]) -> float:
    peak = float("-inf")
    max_dd = 0.0
    for e in equity:
        peak = max(peak, e)
        if peak > 0:
            dd = (peak - e) / peak
            max_dd = max(max_dd, dd)
    return max_dd


def _write_csv(path: Path, rows: list[dict[str, Any]], columns: list[str]) -> None:
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def write_artifact_manifest(run_dir: str | Path, run_name: str | None = None) -> Path:
    """Write a content-addressed manifest for one completed result directory."""
    root = Path(run_dir)
    artifacts = []
    for path in sorted(p for p in root.rglob("*") if p.is_file() and p.name != "artifact_manifest.json"):
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        artifacts.append({
            "path": path.relative_to(root).as_posix(),
            "size_bytes": path.stat().st_size,
            "sha256": digest.hexdigest(),
        })
    manifest = {"run_name": run_name or root.name, "artifacts": artifacts}
    output = root / "artifact_manifest.json"
    output.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return output


def _write_series(out: Path, stem: str, rows: list[dict[str, Any]], columns: list[str]) -> str:
    """Persist a large per-bar series (``equity_curve``/``signals``).

    Hybrid storage: these two streams have one row per bar (~1M rows over 2 years),
    are never eyeballed row-by-row, and dominate on-disk size — so we store them as
    parquet (≈1/8 the size, faster to load for downstream analysis). The small,
    human-facing streams (trades/fills/intents/positions + metrics/report/config)
    stay CSV so they can be opened directly on the server.

    Falls back to CSV when polars is unavailable (e.g. local dev without polars) so
    nothing breaks. Returns the basename actually written (``<stem>.parquet`` or
    ``<stem>.csv``) — readers try parquet first, then csv.
    """
    path = out / f"{stem}.parquet"
    try:  # preferred: polars (server-canonical)
        import polars as pl  # noqa: PLC0415

        if rows:
            frame = pl.DataFrame(rows)
            keep = [c for c in columns if c in frame.columns]
            frame.select(keep).write_parquet(path)
        else:
            pl.DataFrame({c: [] for c in columns}).write_parquet(path)
        return f"{stem}.parquet"
    except Exception:
        pass
    try:  # fallback: pyarrow (present without polars too)
        import pyarrow as pa  # noqa: PLC0415
        import pyarrow.parquet as pq  # noqa: PLC0415

        table = pa.table({c: [r.get(c) for r in rows] for c in columns})
        pq.write_table(table, path)
        return f"{stem}.parquet"
    except Exception:
        pass
    _write_csv(out / f"{stem}.csv", rows, columns)  # last resort: csv
    return f"{stem}.csv"


def _dump_config(path_base: Path, config: dict[str, Any] | None) -> tuple[str, str]:
    """Write the resolved config as YAML when possible, else JSON. Returns (key, path)."""
    if config is None:
        config = {}
    try:
        import yaml  # noqa: PLC0415

        path = path_base.with_name("config.yaml")
        path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
        return "config", str(path)
    except Exception:
        path = path_base.with_name("config.json")
        path.write_text(json.dumps(config, indent=2, default=str), encoding="utf-8")
        return "config", str(path)


def write_backtest_report(
    *,
    output_dir: str | Path,
    run_name: str,
    mode: str,
    backend: str,
    initial_cash: float,
    bars: list[dict[str, Any]],
    signals: list[dict[str, Any]],
    intents: list[dict[str, Any]],
    fills: list[FillRecord],
    feature_names: list[str] | None = None,
    fee_rate: float = 0.0,
    slippage_bps: float = 0.0,
    fill_timing: str = "same_bar",
    execution_stats: dict[str, Any] | None = None,
    funding_events: list[Any] | None = None,
    config: dict[str, Any] | None = None,
    engine_summary: dict[str, Any] | None = None,
) -> BacktestResult:
    """Compute analytics from fills+bars and write the full artifact set.

    ``bars`` rows: ``{event_time_ns, instrument_id, open, high, low, close, volume}``.
    ``signals`` rows: ``{event_time_ns, instrument_id, signal, close, <feature...>}``.
    ``intents`` rows: ``{event_time_ns, instrument_id, action, quantity, reason}``.
    ``fills``: :class:`FillRecord` list from the simulated or native engine.
    """
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    feature_names = list(feature_names or [])
    funding_sorted = sorted(funding_events or [], key=lambda event: event.event_time_ns)

    fills_sorted = sorted(fills, key=lambda f: (f.event_time_ns or 0))
    acct = _Accountant(initial_cash, fee_rate=fee_rate)

    # Build the equity curve by walking bars in time order, applying any fills
    # whose timestamp has been reached, then marking to the bar close.
    fi = 0
    funding_i = 0
    funding_rows: list[dict[str, Any]] = []
    equity_rows: list[dict[str, Any]] = []
    equity_values: list[float] = []
    bars_sorted = sorted(bars, key=lambda b: (b.get("event_time_ns") or 0))
    last_marks: dict[str, float] = {}

    for bar in bars_sorted:
        ts = bar.get("event_time_ns") or 0
        iid = bar.get("instrument_id")
        close = float(bar.get("close") or 0.0)
        last_marks[iid] = close
        while fi < len(fills_sorted) and (fills_sorted[fi].event_time_ns or 0) <= ts:
            acct.apply_fill(fills_sorted[fi])
            fi += 1
        acct.mark(iid, close)
        while funding_i < len(funding_sorted) and funding_sorted[funding_i].event_time_ns <= ts:
            funding = funding_sorted[funding_i]
            mark = funding.mark_price
            if mark is None:
                mark = last_marks.get(funding.instrument_id, close)
            position = acct.position(funding.instrument_id).qty
            payment = acct.apply_funding(funding, float(mark))
            funding_rows.append({
                "event_time_ns": funding.event_time_ns,
                "event_time": _ns_to_iso(funding.event_time_ns),
                "instrument_id": funding.instrument_id,
                "position": position,
                "mark_price": float(mark),
                "funding_rate": funding.funding_rate,
                "funding_payment": payment,
                "cumulative_funding": acct.total_funding,
            })
            funding_i += 1
        pos = acct.position(iid)
        equity = acct.equity(last_marks)
        equity_values.append(equity)
        equity_rows.append(
            {
                "event_time_ns": ts,
                "event_time": _ns_to_iso(ts),
                "instrument_id": iid,
                "close": close,
                "cash": round(acct.cash, 8),
                "position": round(pos.qty, 10),
                "realized_pnl": round(acct.realized_total(), 8),
                "commission": round(acct.total_commission, 8),
                "funding_pnl": round(acct.total_funding, 8),
                "unrealized_pnl": round(acct.unrealized(last_marks), 8),
                "net_pnl": round(equity - float(initial_cash), 8),
                "equity": round(equity, 8),
            }
        )

    # Apply any remaining fills that fall after the last bar.
    while fi < len(fills_sorted):
        acct.apply_fill(fills_sorted[fi])
        fi += 1

    final_equity = acct.equity(last_marks) if last_marks else acct.cash
    if equity_values:
        equity_values[-1] = final_equity

    # Final positions snapshot.
    final_positions: list[dict[str, Any]] = []
    for iid, pos in sorted(acct._pos.items()):
        mark = last_marks.get(iid, pos.last_price)
        if pos.qty != 0:
            final_positions.append(
                {
                    "instrument_id": iid,
                    "quantity": round(pos.qty, 10),
                    "avg_price": round(pos.avg, 8),
                    "market_price": round(mark, 8),
                    "unrealized_pnl": round(pos.qty * (mark - pos.avg), 8),
                    "realized_pnl": round(pos.realized, 8),
                }
            )

    trades = acct.trades
    wins = sum(1 for t in trades if t.win)
    trade_count = len(trades)
    start_ns = bars_sorted[0]["event_time_ns"] if bars_sorted else None
    end_ns = bars_sorted[-1]["event_time_ns"] if bars_sorted else None
    actionable = sum(1 for s in signals if s.get("signal") in _ACTIONABLE)

    gross_realized = acct.realized_total()          # price PnL, EXCLUDING fees
    unrealized = acct.unrealized(last_marks)        # mark-to-market, gross
    total_commission = acct.total_commission        # charged once (in cash)
    total_funding = acct.total_funding
    net_realized = gross_realized - total_commission + total_funding
    net_pnl = final_equity - float(initial_cash)    # == net_realized + unrealized
    gross_win_rate = round(wins / trade_count, 6) if trade_count else None

    metrics: dict[str, Any] = {
        "run_name": run_name,
        "mode": mode,
        "backend": backend,
        "initial_cash": float(initial_cash),
        "final_equity": round(final_equity, 8),
        "total_return": round((final_equity / initial_cash - 1.0) if initial_cash else 0.0, 8),
        "max_drawdown": round(_max_drawdown(equity_values), 8),
        # ``realized_pnl`` is GROSS (price only, no fees) - kept for back-compat.
        "realized_pnl": round(gross_realized, 8),
        "gross_realized_pnl": round(gross_realized, 8),
        "total_commission": round(total_commission, 8),
        "funding_pnl": round(total_funding, 8),
        "funding_event_count": len(funding_rows),
        "net_realized_pnl": round(net_realized, 8),
        "unrealized_pnl": round(unrealized, 8),
        # net_pnl == final_equity - initial_cash == net_realized + unrealized.
        "net_pnl": round(net_pnl, 8),
        "trade_count": trade_count,
        # win_rate is GROSS (judged on per-trade gross realized PnL); see basis.
        "win_rate": gross_win_rate,
        "gross_win_rate": gross_win_rate,
        "win_rate_basis": "gross",
        "fill_count": len(fills_sorted),
        "bar_count": len(bars_sorted),
        "signal_count": actionable,
        "signal_breakdown": _count_signals(signals),
        "start_time_ns": start_ns,
        "end_time_ns": end_ns,
        "start_time": _ns_to_iso(start_ns),
        "end_time": _ns_to_iso(end_ns),
        "fee_rate": float(fee_rate),
        "slippage_bps": float(slippage_bps),
        # Execution-timing provenance (same_bar legacy default; next_bar shifts
        # execution to the following bar). Counts come from the backend.
        "fill_timing": fill_timing,
    }
    if execution_stats:
        for k in ("original_intent_count", "executed_intent_count", "dropped_tail_intents", "latency_bars"):
            if k in execution_stats:
                metrics[k] = execution_stats[k]
    if engine_summary:
        metrics["engine"] = engine_summary

    # ---- write artifacts --------------------------------------------------
    files: dict[str, str] = {}

    signal_cols = ["event_time_ns", "event_time", "instrument_id", "signal", "close", *feature_names]
    signal_rows = [{**s, "event_time": _ns_to_iso(s.get("event_time_ns"))} for s in signals]
    signals_name = _write_series(out, "signals", signal_rows, signal_cols)
    files["signals"] = str(out / signals_name)

    intent_rows = [{**i, "event_time": _ns_to_iso(i.get("event_time_ns"))} for i in intents]
    _write_csv(
        out / "intents.csv",
        intent_rows,
        ["event_time_ns", "event_time", "instrument_id", "action", "quantity", "reason"],
    )
    files["intents"] = str(out / "intents.csv")

    fill_rows = [
        {
            "event_time_ns": f.event_time_ns,
            "event_time": _ns_to_iso(f.event_time_ns),
            "instrument_id": f.instrument_id,
            "side": f.side,
            "quantity": f.quantity,
            "fill_price": f.price,
            "commission": float((f.metadata or {}).get("commission") or 0.0),
            "source": f.source,
        }
        for f in fills_sorted
    ]
    for row in fill_rows:
        if row["commission"] == 0.0 and fee_rate:
            row["commission"] = row["quantity"] * row["fill_price"] * fee_rate
    _write_csv(
        out / "trades.csv",
        [
            {
                "instrument_id": t.instrument_id,
                "side": t.side,
                "quantity": t.quantity,
                "entry_time_ns": t.entry_time_ns,
                "entry_time": _ns_to_iso(t.entry_time_ns),
                "exit_time_ns": t.exit_time_ns,
                "exit_time": _ns_to_iso(t.exit_time_ns),
                "entry_price": round(t.entry_price, 8),
                "exit_price": round(t.exit_price, 8),
                "realized_pnl": round(t.realized_pnl, 8),
                "win": t.win,
            }
            for t in trades
        ],
        ["instrument_id", "side", "quantity", "entry_time_ns", "entry_time", "exit_time_ns",
         "exit_time", "entry_price", "exit_price", "realized_pnl", "win"],
    )
    files["trades"] = str(out / "trades.csv")

    _write_csv(out / "fills.csv", fill_rows,
               ["event_time_ns", "event_time", "instrument_id", "side", "quantity",
                "fill_price", "commission", "source"])
    files["fills"] = str(out / "fills.csv")

    _write_csv(
        out / "funding_payments.csv", funding_rows,
        ["event_time_ns", "event_time", "instrument_id", "position", "mark_price",
         "funding_rate", "funding_payment", "cumulative_funding"],
    )
    files["funding_payments"] = str(out / "funding_payments.csv")

    _write_csv(
        out / "positions.csv",
        final_positions,
        ["instrument_id", "quantity", "avg_price", "market_price", "unrealized_pnl", "realized_pnl"],
    )
    files["positions"] = str(out / "positions.csv")

    equity_name = _write_series(
        out, "equity_curve", equity_rows,
        ["event_time_ns", "event_time", "instrument_id", "close", "cash", "position",
         "realized_pnl", "commission", "funding_pnl", "unrealized_pnl", "net_pnl", "equity"],
    )
    files["equity_curve"] = str(out / equity_name)

    metrics_path = out / "metrics.json"
    metrics_path.write_text(json.dumps(metrics, indent=2, default=str), encoding="utf-8")
    files["metrics"] = str(metrics_path)

    key, cfg_path = _dump_config(out / "config.yaml", config)
    files[key] = cfg_path

    report_path = out / "report.md"
    report_path.write_text(_render_report_md(metrics, trades, final_positions), encoding="utf-8")
    files["report"] = str(report_path)

    # Persist PnL charts alongside the row's data (equity / drawdown / pnl / position
    # PNGs under ``<run>/charts/``). Best-effort: matplotlib is optional — if it is
    # not installed ``render_run_charts`` returns ``{}`` and the run still succeeds.
    from results.charts import render_run_charts  # noqa: PLC0415

    for name, rel in render_run_charts(out).items():
        files[f"chart_{name}"] = str(out / rel)
    manifest_path = write_artifact_manifest(out, run_name)
    files["artifact_manifest"] = str(manifest_path)

    return BacktestResult(
        run_name=run_name,
        output_dir=out,
        metrics=metrics,
        files=files,
        trades=trades,
        equity_curve=equity_rows,
        final_positions=final_positions,
    )


def _count_signals(signals: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for s in signals:
        sig = s.get("signal", "HOLD")
        counts[sig] = counts.get(sig, 0) + 1
    return counts


def _render_report_md(metrics: dict[str, Any], trades: list[TradeRow],
                      positions: list[dict[str, Any]]) -> str:
    m = metrics
    lines = [
        f"# Backtest Report - {m['run_name']}",
        "",
        f"- **Backend:** `{m['backend']}`  **Mode:** `{m['mode']}`  "
        f"**Fill timing:** `{m.get('fill_timing', 'same_bar')}`",
        f"- **Period:** {m.get('start_time')} -> {m.get('end_time')}  ({m['bar_count']} bars)",
        "",
        "## Metrics",
        "",
        "| metric | value |",
        "| --- | --- |",
        f"| initial_cash | {m['initial_cash']:.2f} |",
        f"| final_equity | {m['final_equity']:.2f} |",
        f"| total_return | {m['total_return']:.4%} |",
        f"| max_drawdown | {m['max_drawdown']:.4%} |",
        f"| realized_pnl (gross) | {m['realized_pnl']:.4f} |",
        f"| total_commission | {m.get('total_commission', 0.0):.4f} |",
        f"| funding_pnl | {m.get('funding_pnl', 0.0):.4f} |",
        f"| net_realized_pnl | {m.get('net_realized_pnl', 0.0):.4f} |",
        f"| unrealized_pnl | {m['unrealized_pnl']:.4f} |",
        f"| net_pnl | {m.get('net_pnl', 0.0):.4f} |",
        f"| trade_count | {m['trade_count']} |",
        f"| win_rate (gross) | {('%.2f%%' % (m['win_rate'] * 100)) if m['win_rate'] is not None else 'n/a'} |",
        f"| fill_count | {m['fill_count']} |",
        f"| signal_count (actionable) | {m['signal_count']} |",
        f"| bar_count | {m['bar_count']} |",
        "",
        f"Signal breakdown: `{m['signal_breakdown']}`",
        "",
    ]
    if m.get("engine"):
        lines += ["## Native engine summary", "", "```json",
                  json.dumps(m["engine"], indent=2, default=str), "```", ""]
    lines += ["## Trades", "", f"{len(trades)} closed trade(s)."]
    if trades:
        lines += ["", "| side | qty | entry | exit | pnl | win |", "| --- | --- | --- | --- | --- | --- |"]
        for t in trades:
            lines.append(
                f"| {t.side} | {t.quantity:g} | {t.entry_price:.4f} | {t.exit_price:.4f} "
                f"| {t.realized_pnl:.4f} | {'Y' if t.win else 'N'} |"
            )
    lines += ["", "## Final positions", ""]
    if positions:
        lines += ["| instrument | qty | avg | mark | uPnL |", "| --- | --- | --- | --- | --- |"]
        for p in positions:
            lines.append(
                f"| {p['instrument_id']} | {p['quantity']:g} | {p['avg_price']:.4f} "
                f"| {p['market_price']:.4f} | {p['unrealized_pnl']:.4f} |"
            )
    else:
        lines.append("Flat at end of run.")
    lines.append("")
    return "\n".join(lines)
