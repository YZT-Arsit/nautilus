from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from nautilus_ext.discovery.timeframe_inferencer import TimeframeInferencer


def main() -> None:
    assert TimeframeInferencer.infer_from_path("IH2303_CFFEX_1min_bars.csv") == "1-MINUTE"
    assert TimeframeInferencer.infer_from_path("IH2303_CFFEX_5min_bars.parquet") == "5-MINUTE"
    assert TimeframeInferencer.infer_from_path("bars_0060S.csv") == "1-MINUTE"
    print("generated bar timeframe path tests ok")


if __name__ == "__main__":
    main()
