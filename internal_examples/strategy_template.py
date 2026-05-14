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
    Slightly different template which records how many bars were seen.

    Keep this simple; real order logic belongs in your own Nautilus Strategy,
    not in this example template.
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
