from __future__ import annotations
from nautilus_ext.strategies.base_bar_strategy import BaseBarStrategy
from nautilus_ext.strategies.vwm_short_signals import VwmShortSignalConfig
from nautilus_ext.strategies.vwm_short_signals import VolumeWeightedMomentumShortSignalEngine
class StrategyTemplate(BaseBarStrategy):
    def __init__(self, bar_type, **params):
        strategy_kind = params.get("strategy_kind", "vwm_short")
        if strategy_kind != "vwm_short":
            raise ValueError(
                f"Unsupported strategy_kind={strategy_kind!r}. "
                "This template currently implements 'vwm_short'.",
            )

        signal_engine = VolumeWeightedMomentumShortSignalEngine(
            VwmShortSignalConfig(
                mom_len=int(params.get("mom_len", 5)),
                avg_len=int(params.get("avg_len", 20)),
                atr_len=int(params.get("atr_len", 5)),
                atr_pcnt=float(params.get("atr_pcnt", 0.5)),
                setup_len=int(params.get("setup_len", 5)),
            ),
        )
        super().__init__(
            bar_type=bar_type,
            signal_engine=signal_engine,
            trade_size=params.get("trade_size", 1),
            order_quantity_precision=params.get("order_quantity_precision"),
        )
        self.params = params
        self.strategy_kind = strategy_kind