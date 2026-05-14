from nautilus_trader.trading.strategy import Strategy

class StrategyTemplate(Strategy):
    def __init__(self, bar_type, **params):
        super().__init__()
        self.bar_type = bar_type
        self.params = params
        self.bar_count = 0

    def on_start(self):
        self.subscribe_bars(self.bar_type)

    def on_bar(self, bar):
        self.bar_count += 1
        # TODO: write strategy logic here.
        pass

    def on_stop(self):
        pass