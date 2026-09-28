"""Shared-market-data forward paper orchestration.

One causal bar clock is built per symbol/timeframe, then fanned out to the
frozen logical strategy cases.  Every candidate owns two isolated virtual
accounts.  No production execution adapter is imported or constructed.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import time
from collections import defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import pandas as pd
import yaml

from data_engine.events import BarEvent, FundingRateEvent, QuoteEvent, TradeEvent
from feature_engine.runner import FeatureStrategyRunner
from scripts.internal.run_all_strategy_timeframe_lag import _build_config_obj
from strategy_framework.execution.intents import PlannedSignal
from strategy_framework.execution.reports import ExecutionReport, FillRecord
from strategy_framework.paper_trading.core import (
    AppendOnlyMarketDataRecorder,
    CausalBarAggregator,
    ExchangeFilter,
    FirstTickShadowExecutor,
    MakerPaperExecutor,
    PaperAccount,
    deterministic_event_id,
)
from strategy_framework.registry import get_entry


class _NullRecorder:
    def append(self, _event: Any) -> bool: return True
    def sync(self) -> None: return None
    def close(self) -> None: return None
    def manifest(self) -> list[dict[str, Any]]: return []


def _json_line(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, sort_keys=True, default=str, separators=(",", ":")) + "\n")
        handle.flush()


def _sign(value: float) -> float:
    return 1.0 if value > 0 else -1.0 if value < 0 else 0.0


def make_usdm_instrument(symbol: str, exchange_info: dict[str, Any], maker_fee: float = 0.0):
    """Build a native Nautilus perpetual from a read-only exchangeInfo snapshot."""
    from decimal import Decimal
    from nautilus_trader.model.identifiers import InstrumentId, Symbol, Venue
    from nautilus_trader.model.instruments import CryptoPerpetual
    from nautilus_trader.model.objects import Currency, Money, Price, Quantity

    raw = next(row for row in exchange_info["symbols"] if row["symbol"] == symbol)
    filters = {row["filterType"]: row for row in raw["filters"]}
    price = filters["PRICE_FILTER"]
    qty = filters["LOT_SIZE"]
    notional = filters.get("MIN_NOTIONAL", {})
    usdt = Currency.from_str("USDT")
    return CryptoPerpetual(
        instrument_id=InstrumentId(symbol=Symbol(f"{symbol}-PERP"), venue=Venue("BINANCE")),
        raw_symbol=Symbol(symbol), base_currency=Currency.from_str(raw["baseAsset"]),
        quote_currency=usdt, settlement_currency=usdt, is_inverse=False,
        price_precision=int(raw["pricePrecision"]), price_increment=Price.from_str(price["tickSize"]),
        size_precision=int(raw["quantityPrecision"]), size_increment=Quantity.from_str(qty["stepSize"]),
        max_quantity=Quantity.from_str(qty["maxQty"]), min_quantity=Quantity.from_str(qty["minQty"]),
        max_notional=None, min_notional=Money(float(notional.get("notional", 0.0)), usdt),
        max_price=Price.from_str(price["maxPrice"]), min_price=Price.from_str(price["minPrice"]),
        margin_init=Decimal("1.00"), margin_maint=Decimal("0.35"),
        maker_fee=Decimal(str(maker_fee)), taker_fee=Decimal(str(maker_fee)),
        ts_event=int(raw.get("onboardDate", 0)) * 1_000_000,
        ts_init=int(raw.get("onboardDate", 0)) * 1_000_000,
    )


def exchange_filter(symbol: str, exchange_info: dict[str, Any], observed_at_ns: int) -> ExchangeFilter:
    raw = next(row for row in exchange_info["symbols"] if row["symbol"] == symbol)
    filters = {row["filterType"]: row for row in raw["filters"]}
    return ExchangeFilter(
        tick_size=float(filters["PRICE_FILTER"]["tickSize"]),
        step_size=float(filters["LOT_SIZE"]["stepSize"]),
        min_qty=float(filters["LOT_SIZE"]["minQty"]),
        min_notional=float(filters.get("MIN_NOTIONAL", {}).get("notional", 0.0)),
        observed_at_ns=observed_at_ns,
        source="BINANCE_USDM_EXCHANGE_INFO",
    )


@dataclass
class CandidatePortfolio:
    candidate_id: str
    symbol: str
    variant: str
    initial_capital: float
    target_notional: float
    fee_rate: float
    instrument_filter: ExchangeFilter
    native_instrument: Any

    def __post_init__(self) -> None:
        iid = f"{self.symbol}-PERP.BINANCE"
        self.first_account = PaperAccount(self.initial_capital, self.target_notional, self.fee_rate)
        self.maker_account = PaperAccount(self.initial_capital, self.target_notional, self.fee_rate)
        self.first = FirstTickShadowExecutor(self.first_account, iid)
        self.maker: MakerPaperExecutor | None = None
        self.latest_quote: QuoteEvent | None = None
        self.last_price = float("nan")
        self.decision_count = 0
        self.funding_count = 0

    def on_quote(self, event: QuoteEvent) -> None:
        self.latest_quote = event
        self.last_price = event.mid_price
        if self.maker is not None:
            self.maker.on_quote(event)

    def on_trade(self, event: TradeEvent) -> tuple[int, int]:
        self.last_price = float(event.price)
        before_first = len(self.first.fills)
        self.first.on_trade(event)
        before_maker = len(self.maker.fills) if self.maker else 0
        if self.maker is not None:
            self.maker.on_trade(event)
        return len(self.first.fills) - before_first, (len(self.maker.fills) - before_maker if self.maker else 0)

    def on_target(self, ts: int, target: float) -> None:
        self.decision_count += 1
        effective = -target if self.variant == "STRICT_REVERSE" else target
        self.first.on_target(ts, effective)
        if self.maker is None and self.latest_quote is not None and not math.isclose(effective, 0.0):
            from strategy_framework.backends.nautilus_maker import NativeMakerHarness
            harness = NativeMakerHarness(
                liquidity_consumption=True, queue_position=False,
                fill_probability=1.0, seed=7, maker_fee_rate=self.fee_rate,
                instrument=self.native_instrument,
            )
            self.maker = MakerPaperExecutor(self.maker_account, self.instrument_filter, harness)
            self.maker.on_quote(self.latest_quote)
        if self.maker is not None:
            self.maker.on_target(ts, effective)

    def on_funding(self, event: FundingRateEvent) -> None:
        mark = float(event.mark_price if event.mark_price is not None else self.last_price)
        if not math.isnan(mark):
            self.first_account.apply_funding(event, mark)
            self.maker_account.apply_funding(event, mark)
            self.funding_count += 1

    def summary(self) -> dict[str, Any]:
        mark = self.last_price if not math.isnan(self.last_price) else 0.0
        maker_orders = list(self.maker.orders) if self.maker else []
        full = sum(str(getattr(o.status, "name", o.status)) == "FILLED" for o in maker_orders)
        partial = sum(float(str(getattr(o, "filled_qty", 0))) > 0 and str(getattr(o.status, "name", o.status)) != "FILLED" for o in maker_orders)
        zero = sum(float(str(getattr(o, "filled_qty", 0))) == 0 for o in maker_orders)
        requested = sum(float(str(getattr(o, "quantity", 0))) for o in maker_orders)
        filled = sum(float(str(getattr(o, "filled_qty", 0))) for o in maker_orders)
        return {
            "experiment_candidate_id": self.candidate_id,
            "symbol": self.symbol, "direction_variant": self.variant,
            "decisions": self.decision_count,
            "FIRST_TICK_fills": len(self.first.fills),
            "FIRST_TICK_Return": (self.first_account.equity(mark) / self.initial_capital - 1.0) if mark else 0.0,
            "FIRST_TICK_total_turnover_raw": self.first_account.total_turnover_raw,
            "MAKER_orders": len(maker_orders), "MAKER_fills": len(self.maker.fills) if self.maker else 0,
            "MAKER_full_fill_orders": full, "MAKER_partial_fill_orders": partial,
            "MAKER_zero_fill_orders": zero,
            "MAKER_quantity_fill_ratio": filled / requested if requested else float("nan"),
            "MAKER_cancels": self.maker.cancels if self.maker else 0,
            "MAKER_unrepresentable_trade_count": self.maker.unrepresentable_trade_count if self.maker else 0,
            "MAKER_Return": (self.maker_account.equity(mark) / self.initial_capital - 1.0) if mark else 0.0,
            "MAKER_total_turnover_raw": self.maker_account.total_turnover_raw,
            "funding_events": self.funding_count,
        }


class LogicalStrategy:
    def __init__(self, row: Any, repo: Path, warmup: list[BarEvent]) -> None:
        self.strategy_id = str(row.strategy_id)
        self.symbol = str(row.symbol)
        self.timeframe = str(row.timeframe)
        plugin = get_entry(self.strategy_id)
        path = Path(plugin.default_config_path)
        if not path.is_absolute():
            path = repo / path
        source = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        config = _build_config_obj(plugin.config_cls, source.get("params", {}), self.timeframe, 0)
        self.strategy = plugin.strategy_cls(config)
        self.runner = FeatureStrategyRunner(list(plugin.build_specs(config)), self.strategy)
        self.runner.warmup(warmup)
        self.virtual_position = 0.0
        self.fallback_target = 0.0
        self.fills: list[FillRecord] = []

    def on_bar(self, event: BarEvent) -> float:
        _, signal = self.runner.on_event(event)
        actions = signal.actions if isinstance(signal, PlannedSignal) else ()
        target = getattr(self.strategy, "decision_position", None)
        new_fills: list[FillRecord] = []
        if actions:
            for action in actions:
                if action.close_all:
                    delta = -self.virtual_position
                else:
                    delta = float(action.quantity) * (1.0 if action.side == "BUY" else -1.0)
                if abs(delta) > 1e-15:
                    fill = FillRecord(
                        instrument_id=event.instrument_id,
                        side="BUY" if delta > 0 else "SELL", quantity=abs(delta),
                        price=float(action.fill_price or event.close), event_time_ns=event.event_time_ns,
                        source="paper_signal_state",
                    )
                    new_fills.append(fill)
                    self.virtual_position += delta
            target = _sign(self.virtual_position)
        elif target is None:
            text = str(signal)
            if text == "BUY": self.fallback_target = 1.0
            elif text == "SELL": self.fallback_target = -1.0
            target = self.fallback_target
            self.virtual_position = float(target)
        else:
            self.virtual_position = float(target)
        if new_fills:
            self.fills.extend(new_fills)
            hook = getattr(self.strategy, "on_execution_report", None)
            if hook is not None:
                hook(ExecutionReport(
                    backend="paper_signal_state", total_intents=len(self.fills), total_fills=len(self.fills),
                    fills=list(self.fills), positions=[], realized_pnl=0.0, unrealized_pnl=0.0,
                ))
        return _sign(float(target or 0.0))


class PaperOrchestrator:
    def __init__(
        self, *, repo: Path, experiment: Path, manifest: pd.DataFrame,
        exchange_info: dict[str, Any], warmup_by_symbol_timeframe: dict[tuple[str, str], list[BarEvent]],
        initial_capital: float = 100_000.0, target_notional: float = 100_000.0,
        fee_rate: float = 0.0, record_market_data: bool = True,
    ) -> None:
        self.repo = Path(repo)
        self.experiment = Path(experiment)
        self.manifest = manifest.copy()
        self.recorder = (
            AppendOnlyMarketDataRecorder(self.experiment / "market_data", "BINANCE_USDM_PRODUCTION_PUBLIC")
            if record_market_data else _NullRecorder()
        )
        self.counts = defaultdict(int)
        self.latest_settled_funding: set[tuple[str, int]] = set()
        self.latest_quote: dict[str, QuoteEvent] = {}
        self.builders: dict[tuple[str, str], CausalBarAggregator] = {}
        self.logicals: dict[tuple[str, str, str], LogicalStrategy] = {}
        self.portfolios: dict[str, CandidatePortfolio] = {}
        now = time.time_ns()
        instruments = {s: make_usdm_instrument(s, exchange_info, fee_rate) for s in manifest.symbol.unique()}
        filters = {s: exchange_filter(s, exchange_info, now) for s in manifest.symbol.unique()}
        logical_rows = manifest.drop_duplicates(["strategy_id", "symbol", "timeframe"])
        for row in logical_rows.itertuples(index=False):
            key = (str(row.strategy_id), str(row.symbol), str(row.timeframe))
            self.logicals[key] = LogicalStrategy(row, self.repo, warmup_by_symbol_timeframe[(key[1], key[2])])
            self.builders.setdefault((key[1], key[2]), CausalBarAggregator(f"{key[1]}-PERP.BINANCE", int(key[2][:-1])))
        for row in manifest.itertuples(index=False):
            self.portfolios[str(row.experiment_candidate_id)] = CandidatePortfolio(
                str(row.experiment_candidate_id), str(row.symbol), str(row.direction_variant),
                initial_capital, target_notional, fee_rate, filters[str(row.symbol)], instruments[str(row.symbol)],
            )
        self.by_symbol = {s: [p for p in self.portfolios.values() if p.symbol == s] for s in manifest.symbol.unique()}
        self.by_logical: dict[tuple[str, str, str], list[CandidatePortfolio]] = defaultdict(list)
        for row in manifest.itertuples(index=False):
            self.by_logical[(str(row.strategy_id), str(row.symbol), str(row.timeframe))].append(
                self.portfolios[str(row.experiment_candidate_id)]
            )

    def on_event(self, event: Any) -> None:
        self.recorder.append(event)
        symbol = str(event.instrument_id).split("-PERP", 1)[0].split(".", 1)[0]
        if isinstance(event, QuoteEvent):
            self.counts["quote_events"] += 1
            self.latest_quote[symbol] = event
            for portfolio in self.by_symbol.get(symbol, ()):
                portfolio.on_quote(event)
        elif isinstance(event, TradeEvent):
            self.counts["trade_events"] += 1
            for portfolio in self.by_symbol.get(symbol, ()):
                first, maker = portfolio.on_trade(event)
                self.counts["first_tick_fills"] += first
                self.counts["maker_fills"] += maker
            for (builder_symbol, timeframe), builder in self.builders.items():
                if builder_symbol != symbol:
                    continue
                for bar in builder.on_trade(event):
                    self._on_bar(symbol, timeframe, bar)
        elif isinstance(event, FundingRateEvent):
            key = (symbol, int(event.event_time_ns))
            if event.event_time_ns <= time.time_ns() and key not in self.latest_settled_funding:
                self.latest_settled_funding.add(key)
                self.counts["funding_events"] += 1
                for portfolio in self.by_symbol.get(symbol, ()):
                    portfolio.on_funding(event)

    def flush(self, watermark_ns: int) -> None:
        for (symbol, timeframe), builder in self.builders.items():
            for bar in builder.flush(watermark_ns):
                self._on_bar(symbol, timeframe, bar)

    def _on_bar(self, symbol: str, timeframe: str, bar: BarEvent) -> None:
        self.counts[f"bars_{timeframe}"] += 1
        _json_line(self.experiment / "bars" / f"{symbol}_{timeframe}.jsonl", asdict(bar))
        for key, logical in self.logicals.items():
            if key[1:] != (symbol, timeframe):
                continue
            target = logical.on_bar(bar)
            for portfolio in self.by_logical[key]:
                portfolio.on_target(bar.event_time_ns, target)
                _json_line(self.experiment / "decisions" / f"{portfolio.candidate_id}.jsonl", {
                    "decision_id": deterministic_event_id("decision", portfolio.candidate_id, bar.event_time_ns),
                    "experiment_candidate_id": portfolio.candidate_id,
                    "event_time_ns": bar.event_time_ns, "target": -target if portfolio.variant == "STRICT_REVERSE" else target,
                })
                self.counts["strategy_decisions"] += 1

    def write_outputs(self, started_ns: int, ended_ns: int, phase: str) -> dict[str, Any]:
        rows = [portfolio.summary() for portfolio in self.portfolios.values()]
        frame = pd.DataFrame(rows)
        frame.to_csv(self.experiment / "strategy_case_summary.csv", index=False)
        execution = {
            **dict(self.counts),
            "maker_orders": int(frame.MAKER_orders.sum()),
            "maker_full_fill_orders": int(frame.MAKER_full_fill_orders.sum()),
            "maker_partial_fill_orders": int(frame.MAKER_partial_fill_orders.sum()),
            "maker_zero_fill_orders": int(frame.MAKER_zero_fill_orders.sum()),
            "maker_quantity_fill_ratio": float(
                frame.MAKER_quantity_fill_ratio.dropna().mean()
            ) if frame.MAKER_quantity_fill_ratio.notna().any() else None,
        }
        pd.DataFrame([execution]).to_csv(self.experiment / "execution_summary.csv", index=False)
        data_rows = self.recorder.manifest()
        pd.DataFrame(data_rows).to_csv(self.experiment / "data_quality_summary.csv", index=False)
        summary = {
            "phase": phase, "started_ns": started_ns, "ended_ns": ended_ns,
            "duration_hours": (ended_ns - started_ns) / 3.6e12,
            "candidate_count": len(self.manifest), "symbols": sorted(self.manifest.symbol.unique()),
            "production_market_data": "READ_ONLY", "production_exchange_orders": 0,
            **execution,
        }
        pd.DataFrame([summary]).to_csv(self.experiment / "experiment_summary.csv", index=False)
        (self.experiment / "health" / f"{phase}_final.json").write_text(json.dumps(summary, indent=2) + "\n")
        return summary


def manifest_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()
