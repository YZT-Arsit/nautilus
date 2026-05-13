from dataclasses import dataclass

import pandas as pd


@dataclass(frozen=True)
class BarFieldMapping:
    timestamp: str
    open: str
    high: str
    low: str
    close: str
    volume: str | None = None
    symbol: str | None = None


class BarDataAdapter:
    def __init__(
        self,
        mapping: BarFieldMapping,
        timezone: str = "UTC",
        timestamp_unit: str | None = None,
        source_timezone: str | None = None,
    ):
        self.mapping = mapping
        self.timezone = timezone
        self.timestamp_unit = timestamp_unit
        self.source_timezone = source_timezone

    def normalize(self, df: pd.DataFrame) -> pd.DataFrame:
        if df is None:
            raise ValueError("Input DataFrame is required.")

        required_fields = {
            "timestamp": self.mapping.timestamp,
            "open": self.mapping.open,
            "high": self.mapping.high,
            "low": self.mapping.low,
            "close": self.mapping.close,
        }
        missing = [source for source in required_fields.values() if source not in df.columns]
        if missing:
            raise ValueError(f"Missing required bar field columns: {missing}.")

        rename_map = {source: target for target, source in required_fields.items()}
        normalized = df.rename(columns=rename_map).copy()

        if self.mapping.volume is not None:
            if self.mapping.volume not in normalized.columns:
                raise ValueError(f"Missing volume field column: {self.mapping.volume}.")
            normalized = normalized.rename(columns={self.mapping.volume: "volume"})
        else:
            normalized["volume"] = 0.0

        normalized["timestamp"] = self._normalize_timestamps(normalized["timestamp"])
        if normalized["timestamp"].isna().any():
            raise ValueError("Timestamp field contains null or invalid values.")

        normalized = normalized.sort_values("timestamp")
        normalized = normalized.drop_duplicates(subset="timestamp", keep="last")
        normalized = normalized.set_index("timestamp")
        normalized = normalized[["open", "high", "low", "close", "volume"]]

        try:
            for column in ["open", "high", "low", "close", "volume"]:
                normalized[column] = pd.to_numeric(normalized[column], errors="raise")
        except Exception as exc:
            raise ValueError(f"Bar OHLCV fields must be numeric: {exc}") from exc

        if normalized[["open", "high", "low", "close"]].isna().any().any():
            raise ValueError("Bar OHLC fields must not contain null values.")

        normalized["volume"] = normalized["volume"].fillna(0.0)
        if normalized["volume"].isna().any():
            raise ValueError("Bar volume field contains null values after fill.")

        invalid_high_low = normalized["high"] < normalized["low"]
        if invalid_high_low.any():
            first_bad_timestamp = normalized.index[invalid_high_low][0]
            raise ValueError(
                "Invalid OHLC data: high must be greater than or equal to low "
                f"at timestamp {first_bad_timestamp}."
            )

        normalized = normalized.astype(float)

        if normalized.empty:
            raise ValueError("No bar data after normalization.")

        if not normalized.index.is_monotonic_increasing:
            raise ValueError("Timestamp index must be monotonically increasing.")

        return normalized

    def _normalize_timestamps(self, timestamps: pd.Series) -> pd.Series:
        if self.timestamp_unit is not None:
            return pd.to_datetime(timestamps, unit=self.timestamp_unit, utc=True)

        if self.source_timezone is not None:
            converted = pd.to_datetime(timestamps)
            if converted.dt.tz is None:
                converted = converted.dt.tz_localize(self.source_timezone)
            return converted.dt.tz_convert("UTC")

        return pd.to_datetime(timestamps, utc=True)
