import csv
import json
from pathlib import Path


class NautilusComparisonReporter:
    def __init__(
        self,
        run_results: list,
        output_dir: str | Path,
    ):
        self.run_results = run_results
        self.output_dir = Path(output_dir)

    def export(self) -> dict[str, str]:
        self.output_dir.mkdir(parents=True, exist_ok=True)

        rows = [self._row_from_result(result) for result in self.run_results]
        columns = self._columns(rows)

        csv_path = self.output_dir / "comparison_summary.csv"
        json_path = self.output_dir / "comparison_summary.json"
        readme_path = self.output_dir / "README.md"

        with csv_path.open("w", encoding="utf-8", newline="") as file:
            writer = csv.DictWriter(file, fieldnames=columns)
            writer.writeheader()
            writer.writerows(rows)

        with json_path.open("w", encoding="utf-8") as file:
            json.dump(rows, file, indent=2, default=str)

        with readme_path.open("w", encoding="utf-8") as file:
            file.write(
                "# Multi-Strategy Independent Backtest Comparison\n\n"
                "This summary compares independent strategy backtest runs. "
                "Each strategy was run with a fresh native Nautilus BacktestEngine "
                "and a fresh strategy instance. This is not a same-engine portfolio "
                "run with multiple strategies trading together.\n"
            )

        return {
            "comparison_summary_csv": str(csv_path),
            "comparison_summary_json": str(json_path),
            "readme": str(readme_path),
        }

    @staticmethod
    def _row_from_result(result) -> dict:
        row = {
            "run_id": result.run_id,
            "strategy_name": result.strategy_name,
            "bars_count": result.bars_count,
            "bar_type": str(result.bar_type),
            "report_dir": result.report_dir,
            "status": result.status,
        }
        metrics = result.metrics or {"available": False}
        for key, value in metrics.items():
            row[f"metric_{key}"] = value
        return row

    @staticmethod
    def _columns(rows: list[dict]) -> list[str]:
        base_columns = [
            "run_id",
            "strategy_name",
            "bars_count",
            "bar_type",
            "report_dir",
            "status",
        ]
        extra_columns = sorted(
            {
                column
                for row in rows
                for column in row
                if column not in base_columns
            }
        )
        return base_columns + extra_columns
