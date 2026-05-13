from pathlib import Path


class PathScanner:
    SUPPORTED_SUFFIXES = {".csv", ".parquet"}

    def __init__(self, root_path: str | Path):
        self.root_path = Path(root_path)

    def scan_files(self) -> list[Path]:
        if self.root_path.is_file():
            files = [self.root_path]
        elif self.root_path.is_dir():
            files = [
                path
                for path in self.root_path.rglob("*")
                if path.is_file() and path.suffix.lower() in self.SUPPORTED_SUFFIXES
            ]
        else:
            raise ValueError(f"Path does not exist: {self.root_path}")

        files = sorted(files)
        if not files:
            raise ValueError(f"No CSV or Parquet files found under: {self.root_path}")

        return files
