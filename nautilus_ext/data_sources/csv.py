import pandas as pd

from nautilus_ext.data_sources.base import DataSource


class CsvDataSource(DataSource):
    def __init__(self, path: str, **read_csv_kwargs):
        self.path = path
        self.read_csv_kwargs = read_csv_kwargs

    def load(self) -> pd.DataFrame:
        return pd.read_csv(self.path, **self.read_csv_kwargs)
