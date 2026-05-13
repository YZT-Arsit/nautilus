import json
from pathlib import Path


class NautilusResultReporter:
    def __init__(self, run_result):
        self.run_result = run_result

    def export(self, output_dir: str | Path) -> dict[str, str]:
        report_dir = Path(output_dir)
        report_dir.mkdir(parents=True, exist_ok=True)

        engine = self.run_result.engine
        run_info = {
            "run_id": self.run_result.run_id,
            "strategy_name": self.run_result.strategy_name,
            "status": self.run_result.status,
            "engine_type": type(engine).__name__ if engine is not None else None,
            "bar_type": str(self.run_result.bar_type),
            "bars_count": self.run_result.bars_count,
        }
        if self.run_result.error is not None:
            run_info["error"] = self.run_result.error

        metrics = self.run_result.metrics or {"available": False}

        run_info_path = report_dir / "run_info.json"
        metrics_path = report_dir / "metrics.json"
        with run_info_path.open("w", encoding="utf-8") as file:
            json.dump(run_info, file, indent=2, default=str)
        with metrics_path.open("w", encoding="utf-8") as file:
            json.dump(metrics, file, indent=2, default=str)

        return {
            "run_info": str(run_info_path),
            "metrics": str(metrics_path),
        }
