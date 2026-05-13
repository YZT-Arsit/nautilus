import pandas as pd

from nautilus_ext.adapters.bar_adapter import BarFieldMapping


class SchemaInferencer:
    _TIMESTAMP_CANDIDATES = [
        "DateTime",
        "datetime",
        "timestamp",
        "ts",
        "time",
        "open_time",
        "TimeStamp",
    ]
    _SYMBOL_CANDIDATES = [
        "BinanceCode",
        "symbol",
        "ticker",
        "instrument",
        "instrument_id",
    ]
    _OPEN_CANDIDATES = ["open", "Open", "open_price", "open_px"]
    _HIGH_CANDIDATES = ["high", "High", "high_price", "high_px"]
    _LOW_CANDIDATES = ["low", "Low", "low_price", "low_px"]
    _CLOSE_CANDIDATES = ["close", "Close", "close_price", "close_px"]
    _VOLUME_CANDIDATES = ["volume", "Volume", "vol", "qty", "quantity"]

    @staticmethod
    def infer_bar_mapping(df: pd.DataFrame) -> BarFieldMapping:
        columns = list(df.columns)
        timestamp = SchemaInferencer._first_present(columns, SchemaInferencer._TIMESTAMP_CANDIDATES)
        open_column = SchemaInferencer._first_present(columns, SchemaInferencer._OPEN_CANDIDATES)
        high_column = SchemaInferencer._first_present(columns, SchemaInferencer._HIGH_CANDIDATES)
        low_column = SchemaInferencer._first_present(columns, SchemaInferencer._LOW_CANDIDATES)
        close_column = SchemaInferencer._first_present(columns, SchemaInferencer._CLOSE_CANDIDATES)
        volume_column = SchemaInferencer._first_present(columns, SchemaInferencer._VOLUME_CANDIDATES)
        symbol_column = SchemaInferencer.infer_symbol_column(df)

        missing = []
        if timestamp is None:
            missing.append("timestamp")
        if open_column is None:
            missing.append("open")
        if high_column is None:
            missing.append("high")
        if low_column is None:
            missing.append("low")
        if close_column is None:
            missing.append("close")

        if missing:
            raise ValueError(
                "Unable to infer required bar field mapping "
                f"for fields {missing}. Current columns: {columns}"
            )

        return BarFieldMapping(
            timestamp=timestamp,
            open=open_column,
            high=high_column,
            low=low_column,
            close=close_column,
            volume=volume_column,
            symbol=symbol_column,
        )

    @staticmethod
    def infer_symbol_column(df: pd.DataFrame) -> str | None:
        return SchemaInferencer._first_present(
            list(df.columns),
            SchemaInferencer._SYMBOL_CANDIDATES,
        )

    @staticmethod
    def _first_present(columns: list[str], candidates: list[str]) -> str | None:
        column_set = set(columns)
        for candidate in candidates:
            if candidate in column_set:
                return candidate
        return None
