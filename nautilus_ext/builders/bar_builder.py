import pandas as pd

from nautilus_trader.persistence.wranglers import BarDataWrangler


class NautilusBarBuilder:
    def __init__(self, instrument, bar_type):
        self.instrument = instrument
        self.bar_type = bar_type

    def build(self, df: pd.DataFrame):
        if df is None or df.empty:
            raise ValueError("Cannot build Nautilus bars from an empty DataFrame.")

        return BarDataWrangler(self.bar_type, self.instrument).process(df)
