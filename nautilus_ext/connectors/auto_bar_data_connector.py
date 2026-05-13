from pathlib import Path

import pandas as pd

from nautilus_ext.adapters.bar_adapter import BarDataAdapter
from nautilus_ext.builders.bar_builder import NautilusBarBuilder
from nautilus_ext.builders.bar_type_factory import BarTypeFactory
from nautilus_ext.builders.instrument_builder import InstrumentBuilder
from nautilus_ext.discovery.data_type_inferencer import DataTypeInferencer
from nautilus_ext.discovery.dataset_profile import DatasetProfile
from nautilus_ext.discovery.path_scanner import PathScanner
from nautilus_ext.discovery.schema_inferencer import SchemaInferencer
from nautilus_ext.discovery.timeframe_inferencer import TimeframeInferencer


class NautilusAutoBarDataConnector:
    def __init__(
        self,
        root_path: str,
        instrument,
        symbol: str | None = None,
        start: str | None = None,
        end: str | None = None,
        max_files: int | None = None,
        price_type: str = "LAST",
        source: str = "EXTERNAL",
    ):
        self.root_path = Path(root_path)
        self.instrument = instrument
        self.symbol = symbol
        self.start = start
        self.end = end
        self.max_files = max_files
        self.price_type = price_type
        self.source = source

        self.profile = None
        self.raw_df = None
        self.bar_df = None
        self.bar_type = None
        self.bars = None

    def discover(self) -> DatasetProfile:
        if self.profile is not None:
            return self.profile

        files = PathScanner(self.root_path).scan_files()
        sample_path = files[0]
        file_format = self._infer_file_format(sample_path)
        mixed_format_files = [
            path for path in files if self._infer_file_format(path) != file_format
        ]
        if mixed_format_files:
            raise ValueError(
                "Mixed CSV and Parquet files are not supported in one auto connector run. "
                f"Sample file {sample_path} inferred file_format={file_format!r}. "
                f"Conflicting files: {mixed_format_files}"
            )

        sample_df = self._read_sample(sample_path, file_format)
        sample_columns = list(sample_df.columns)
        data_type = DataTypeInferencer.infer_from_columns(sample_columns)

        if data_type != "bar":
            raise NotImplementedError(
                "Only bar data conversion is implemented. "
                f"Inferred data_type={data_type!r} from sample file {sample_path}. "
                f"Current columns: {sample_columns}"
            )

        try:
            field_mapping = SchemaInferencer.infer_bar_mapping(sample_df)
        except ValueError as exc:
            raise ValueError(
                f"Failed to infer bar schema from sample file {sample_path}. "
                f"Current columns: {sample_columns}. Original error: {exc}"
            ) from exc

        timeframe = TimeframeInferencer.infer_from_path(self.root_path)
        if timeframe is None:
            timeframe = TimeframeInferencer.infer_from_path(sample_path)

        self.profile = DatasetProfile(
            root_path=self.root_path,
            files=files,
            file_format=file_format,
            data_type=data_type,
            timeframe=timeframe,
            sample_columns=sample_columns,
            symbol_column=field_mapping.symbol,
            timestamp_column=field_mapping.timestamp,
            field_mapping=field_mapping,
        )
        return self.profile

    def load_raw_data(self) -> pd.DataFrame:
        profile = self.discover()
        files = profile.files
        if self.max_files is not None:
            files = files[: self.max_files]

        frames = [self._read_file(path, profile.file_format) for path in files]
        if not frames:
            raise ValueError(f"No files selected for loading from: {self.root_path}")

        raw_df = pd.concat(frames, ignore_index=True)
        if raw_df.empty:
            raise ValueError(f"No rows loaded from files: {files}")

        if profile.symbol_column is not None and self.symbol is not None:
            raw_df = raw_df[raw_df[profile.symbol_column].astype(str) == self.symbol]
            if raw_df.empty:
                raise ValueError(
                    f"No rows matched symbol={self.symbol!r} using column "
                    f"{profile.symbol_column!r}. Files: {files}"
                )

        if self.start is not None or self.end is not None:
            raw_df = self._filter_time_range(raw_df, profile)

        self.raw_df = raw_df
        return raw_df

    def prepare_data(self):
        if self.bars is not None:
            return self.bars

        profile = self.discover()
        if profile.data_type != "bar":
            raise NotImplementedError(
                "Only bar data conversion is implemented. "
                f"Inferred data_type={profile.data_type!r}. Current columns: {profile.sample_columns}"
            )

        if profile.timeframe is None:
            sample_path = profile.files[0] if profile.files else self.root_path
            raise ValueError(
                "Unable to infer bar timeframe from path. Expected tokens like "
                "0060S, 0300S, 0001H, or 0001D in the root path or file name. "
                f"Root path: {self.root_path}. Sample file: {sample_path}. "
                f"Current columns: {profile.sample_columns}"
            )

        raw_df = self.load_raw_data()
        self.instrument = InstrumentBuilder.require_existing_instrument(self.instrument)
        self.bar_df = BarDataAdapter(profile.field_mapping).normalize(raw_df)
        self.bar_type = BarTypeFactory.create(
            instrument=self.instrument,
            timeframe=profile.timeframe,
            price_type=self.price_type,
            source=self.source,
        )
        self.bars = NautilusBarBuilder(self.instrument, self.bar_type).build(self.bar_df)
        return self.bars

    def get_bars(self):
        if self.bars is None:
            self.prepare_data()
        return self.bars

    def get_bar_type(self):
        if self.bar_type is None:
            self.prepare_data()
        return self.bar_type

    def get_profile(self):
        return self.discover()

    def _filter_time_range(self, raw_df: pd.DataFrame, profile: DatasetProfile) -> pd.DataFrame:
        timestamp_column = profile.timestamp_column
        if timestamp_column is None or timestamp_column not in raw_df.columns:
            raise ValueError(
                "Cannot apply start/end filters because no timestamp column was inferred. "
                f"Current columns: {list(raw_df.columns)}"
            )

        timestamps = pd.to_datetime(raw_df[timestamp_column], utc=True)
        if self.start is not None:
            raw_df = raw_df[timestamps >= self._to_utc_timestamp(self.start)]
            timestamps = timestamps.loc[raw_df.index]
        if self.end is not None:
            raw_df = raw_df[timestamps <= self._to_utc_timestamp(self.end)]

        if raw_df.empty:
            raise ValueError(
                f"No rows remained after time filtering start={self.start!r}, "
                f"end={self.end!r} using column {timestamp_column!r}."
            )

        return raw_df

    @staticmethod
    def _infer_file_format(path: Path) -> str:
        suffix = path.suffix.lower()
        if suffix == ".parquet":
            return "parquet"
        if suffix == ".csv":
            return "csv"
        return "unknown"

    @staticmethod
    def _read_sample(path: Path, file_format: str) -> pd.DataFrame:
        if file_format == "csv":
            return pd.read_csv(path, nrows=1000)
        if file_format == "parquet":
            return pd.read_parquet(path)

        raise ValueError(f"Unsupported file format for sample file: {path}")

    @staticmethod
    def _read_file(path: Path, file_format: str) -> pd.DataFrame:
        if file_format == "csv":
            return pd.read_csv(path)
        if file_format == "parquet":
            return pd.read_parquet(path)

        raise ValueError(f"Unsupported file format for file: {path}")

    @staticmethod
    def _to_utc_timestamp(value: str) -> pd.Timestamp:
        timestamp = pd.Timestamp(value)
        if timestamp.tzinfo is None:
            return timestamp.tz_localize("UTC")
        return timestamp.tz_convert("UTC")
