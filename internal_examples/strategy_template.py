from __future__ import annotations
from nautilus_ext.strategies.base_bar_strategy import BaseBarStrategy
from nautilus_ext.strategies.strategy_registry import build_signal_engine


class StrategyTemplate(BaseBarStrategy):
    def __init__(self, bar_type, **params):
        strategy_kind = params.get("strategy_kind", "vwm_short")
        signal_engine = build_signal_engine(strategy_kind, params)
        super().__init__(
            bar_type=bar_type,
            signal_engine=signal_engine,
            trade_size=params.get("trade_size", 1),
            order_quantity_precision=params.get("order_quantity_precision"),
        )
        self.params = params
        self.strategy_kind = strategy_kind
