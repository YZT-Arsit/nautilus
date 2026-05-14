from nautilus_trader.trading.strategy import Strategy


class StrategyTemplate(Strategy):
    """
    Minimal Nautilus native Strategy template.

    Boss-friendly rule of thumb: most strategy experiments should only change
    the logic inside on_bar().
    """

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


class CountingStrategyTemplate(Strategy):
    """
    Optional compatibility example which records how many bars were seen.

    This is not a strategy switching controller. For regime/logic switching
    inside one Strategy, see strategy_switching_template.py.
    """

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
