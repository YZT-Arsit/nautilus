"""
Forward-only, market-data-only paper trading primitives.

The package deliberately exposes no exchange execution client.  Production
connectivity is limited to public market data; every order remains a local
Nautilus matching-engine object.
"""

from strategy_framework.paper_trading.core import AppendOnlyMarketDataRecorder
from strategy_framework.paper_trading.core import AtomicPaperStateStore
from strategy_framework.paper_trading.core import CausalBarAggregator
from strategy_framework.paper_trading.core import ExchangeFilter
from strategy_framework.paper_trading.core import FirstTickShadowExecutor
from strategy_framework.paper_trading.core import LiveOrderSubmissionDisabled
from strategy_framework.paper_trading.core import MakerPaperExecutor
from strategy_framework.paper_trading.core import MarketDataGapMonitor
from strategy_framework.paper_trading.core import PaperAccount
from strategy_framework.paper_trading.core import daily_turnover_summary
from strategy_framework.paper_trading.core import deterministic_event_id


__all__ = [
    "AppendOnlyMarketDataRecorder",
    "AtomicPaperStateStore",
    "CausalBarAggregator",
    "ExchangeFilter",
    "FirstTickShadowExecutor",
    "LiveOrderSubmissionDisabled",
    "MakerPaperExecutor",
    "MarketDataGapMonitor",
    "PaperAccount",
    "daily_turnover_summary",
    "deterministic_event_id",
]
