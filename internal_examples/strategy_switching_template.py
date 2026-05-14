from nautilus_trader.trading.strategy import Strategy

class StrategySwitchingTemplate(Strategy):
    def __init__(self, bar_type, **params):
        super().__init__()
        self.bar_type = bar_type
        self.params = params
        self.active_regime = None
        self.bar_count = 0
        self.prices = []

    def on_start(self):
        self.subscribe_bars(self.bar_type)

    def on_bar(self, bar):
        self.bar_count += 1
        self.prices.append(float(bar.close))

        regime = self.detect_regime(bar)

        if regime != self.active_regime:
            self.on_regime_change(self.active_regime, regime)
            self.active_regime = regime

        if regime == "trend":
            self.run_trend_logic(bar)
        elif regime == "mean_reversion":
            self.run_mean_reversion_logic(bar)
        else:
            self.run_neutral_logic(bar)

    def detect_regime(self, bar):
        # TODO: replace with real regime detection.
        return "neutral"

    def on_regime_change(self, old_regime, new_regime):
        # TODO: optional rebalance/close positions on regime switch.
        pass

    def run_trend_logic(self, bar):
        # TODO: trend-following logic.
        pass

    def run_mean_reversion_logic(self, bar):
        # TODO: mean-reversion logic.
        pass

    def run_neutral_logic(self, bar):
        # TODO: neutral/no-trade/risk-control logic.
        pass

    def on_stop(self):
        pass