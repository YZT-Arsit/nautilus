from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class DatasetProfile:
    root_path: Path
    files: list[Path]
    file_format: str
    data_type: str
    timeframe: str | None
    sample_columns: list[str]
    symbol_column: str | None
    timestamp_column: str | None
    field_mapping: object | None
