import re
from pathlib import Path


class TimeframeInferencer:
    _TIMEFRAME_MAP = {
        ("0060", "S"): "1-MINUTE",
        ("0180", "S"): "3-MINUTE",
        ("0300", "S"): "5-MINUTE",
        ("0900", "S"): "15-MINUTE",
        ("1800", "S"): "30-MINUTE",
        ("0001", "H"): "1-HOUR",
        ("0002", "H"): "2-HOUR",
        ("0004", "H"): "4-HOUR",
        ("0006", "H"): "6-HOUR",
        ("0008", "H"): "8-HOUR",
        ("0012", "H"): "12-HOUR",
        ("0001", "D"): "1-DAY",
    }

    @staticmethod
    def infer_from_path(path: str | Path) -> str | None:
        path_text = str(path)
        for match in re.finditer(r"(\d{4})(S|H|D)", path_text, flags=re.IGNORECASE):
            key = (match.group(1), match.group(2).upper())
            timeframe = TimeframeInferencer._TIMEFRAME_MAP.get(key)
            if timeframe is not None:
                return timeframe

        aliases = {
            "min": "MINUTE",
            "minute": "MINUTE",
            "h": "HOUR",
            "hour": "HOUR",
            "d": "DAY",
            "day": "DAY",
        }
        for match in re.finditer(
            r"(?<!\d)(\d+)[_-]?(min|minute|h|hour|d|day)(?![a-z])",
            path_text,
            flags=re.IGNORECASE,
        ):
            count = int(match.group(1))
            unit = aliases[match.group(2).lower()]
            return f"{count}-{unit}"

        return None
