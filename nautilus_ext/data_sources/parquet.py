import pandas as pd

from nautilus_ext.data_sources.base import DataSource


class ParquetDataSource(DataSource):
    def __init__(self, path: str, **read_parquet_kwargs):
        self.path = path
        self.read_parquet_kwargs = read_parquet_kwargs

    def load(self) -> pd.DataFrame:
        return pd.read_parquet(self.path, **self.read_parquet_kwargs)
