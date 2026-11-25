"""Coordinate portfolio events; fills and accounting are provided by Nautilus."""
from dataclasses import replace
from types import SimpleNamespace

import numpy as np
import pandas as pd

from strategy_framework.backends.nautilus_simulation import IntentFillSimulator
from strategy_framework.execution.backtest_report import _Accountant
from strategy_framework.execution.costs import apply_adverse_slippage
from strategy_framework.execution.intents import OrderIntent


def replay(prices, targets, funding, capital, fee_bps, spread_bps, slip_bps,
           funding_prices=None, beta=None, fixed_capital=False):
    simulator = IntentFillSimulator(allow_short=True, backend="BAR_EXECUTION_PROXY")
    account = _Accountant(capital)
    events = {}
    for symbol, records in funding.items():
        for record in records:
            key = (record.event_time_ns, symbol)
            if key in events and events[key] != record:
                raise ValueError(f"conflicting funding settlement {key}")
            events[key] = record
    entries = iter(sorted(events.items()))
    pending = next(entries, None)
    marks = {}
    previous_equity = capital
    rows = []
    for i, (timestamp, price_row) in enumerate(zip(prices.index, prices.to_numpy())):
        requested = targets.iloc[i].to_numpy()
        gross = fees = spread = slippage = turnover = funding_pnl = 0.
        proxy_count = unsettled = 0
        before_mark = account.equity(marks)
        fresh = dict(zip(prices.columns, price_row))
        prior_marks = marks.copy()
        while pending is not None and pending[0][0] <= timestamp.value:
            (event_ns, symbol), event = pending
            if account.position(symbol).qty:
                fallback = np.nan if funding_prices is None else funding_prices.get((event_ns, symbol), np.nan)
                if event.mark_price is not None or np.isfinite(fallback):
                    funding_pnl += account.apply_funding(event, fallback)
                    proxy_count += event.mark_price is None
                else:
                    unsettled += 1
            pending = next(entries, None)
        held = np.array([account.position(symbol).qty for symbol in prices.columns])
        missing = (~np.isfinite(price_row)) & ((held != 0) | (requested != 0))
        for symbol, price in fresh.items():
            if np.isfinite(price):
                marks[symbol] = price
                account.mark(symbol, price)
        value = account.equity(marks)
        gross = value - before_mark - funding_pnl
        mark_pnl = np.array([q * (marks.get(s, 0) - prior_marks.get(s, 0))
                             for s, q in zip(prices.columns, held)])
        if value <= 0 and not fixed_capital:
            raise ValueError(f"portfolio equity exhausted at {timestamp}")
        if not missing.any():
            allocation_base = capital if fixed_capital else value
            for symbol, price, weight, current in zip(prices.columns, price_row, requested, held):
                if not np.isfinite(price):
                    continue
                change = allocation_base * weight / price - current
                if abs(change) <= 1e-12:
                    continue
                order = OrderIntent(symbol, "BUY" if change > 0 else "SELL", abs(change),
                                    timestamp.value, "cross_section_rebalance")
                fill = simulator.on_intent(order, SimpleNamespace(close=price))
                adjusted = apply_adverse_slippage(fill, spread_bps + slip_bps)
                cost_notional = adjusted.price * adjusted.quantity
                delta_fee = cost_notional * fee_bps / 1e4
                adverse = abs(adjusted.price - fill.price) * fill.quantity
                if spread_bps + slip_bps:
                    spread += adverse * spread_bps / (spread_bps + slip_bps)
                    slippage += adverse * slip_bps / (spread_bps + slip_bps)
                account.apply_fill(replace(adjusted, metadata={**adjusted.metadata, "commission": delta_fee}))
                fees += delta_fee
                turnover += cost_notional
        equity = account.equity(marks)
        exposure_base = capital if fixed_capital else equity
        return_base = capital if fixed_capital else previous_equity
        actual = np.array([account.position(s).qty * marks.get(s, 0) / exposure_base for s in prices.columns])
        betas = beta.iloc[i].to_numpy() if beta is not None else np.full(len(actual), np.nan)
        active = actual != 0
        btc_beta = np.dot(actual[active], betas[active]) if np.isfinite(betas[active]).all() else np.nan
        rows.append(dict(execution_time=timestamp, equity=equity, gross_pnl=gross, gross_long_pnl=mark_pnl[held>0].sum(),
                         gross_short_pnl=mark_pnl[held<0].sum(), funding=funding_pnl,
                         fees=fees, spread=spread, slippage=slippage, turnover_usdt=turnover,
                         turnover_equity=turnover/return_base, net_return=(equity-previous_equity)/return_base,
                         long_exposure=actual.clip(0).sum(), short_exposure=actual.clip(max=0).sum(),
                         net_exposure=actual.sum(), gross_exposure=np.abs(actual).sum(), btc_beta=btc_beta,
                         funding_mark_proxy_count=proxy_count, unsettled_funding=unsettled,
                         skipped=bool(missing.any()), stale_held_marks=int(((held != 0) & missing).sum())))
        previous_equity = equity
    return pd.DataFrame(rows).set_index("execution_time", drop=False)
