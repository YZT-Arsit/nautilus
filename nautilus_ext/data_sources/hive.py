import pandas as pd

from nautilus_ext.data_sources.base import DataSource


class HiveDataSource(DataSource):
    def __init__(self, query: str, spark=None, sql_engine=None):
        self.query = query
        self.spark = spark
        self.sql_engine = sql_engine

    def load(self) -> pd.DataFrame:
        if self.spark is not None:
            return self.spark.sql(self.query).toPandas()

        if self.sql_engine is not None:
            return pd.read_sql(self.query, self.sql_engine)

        raise ValueError("HiveDataSource requires either a Spark session or a SQL engine.")
