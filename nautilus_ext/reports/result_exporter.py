import json
from pathlib import Path


class ResultExporter:
    def __init__(self, engine):
        self.engine = engine

    def export_placeholder(self, output_dir: str):
        output_path = Path(output_dir)
        output_path.mkdir(parents=True, exist_ok=True)

        run_info = {
            "status": "completed",
            "engine_type": type(self.engine).__name__,
        }

        trader_id = getattr(self.engine, "trader_id", None)
        if trader_id is None:
            trader = getattr(self.engine, "trader", None)
            trader_id = getattr(trader, "id", None) if trader is not None else None
        if trader_id is not None:
            run_info["trader_id"] = str(trader_id)

        run_info_path = output_path / "run_info.json"
        with run_info_path.open("w", encoding="utf-8") as file:
            json.dump(run_info, file, indent=2)

        return run_info_path
