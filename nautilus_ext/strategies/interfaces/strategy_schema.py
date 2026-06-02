from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class StrategyInputSchema:
    input_types: list[str]
    symbols: list[str]
    timeframes: list[str] | None = None
    warmup: dict | int | None = None
    requires_position: bool = True
    requires_portfolio: bool = False
    multi_asset: bool = False
    multi_timeframe: bool = False

    @staticmethod
    def from_dict(values: dict | None) -> "StrategyInputSchema":
        values = dict(values or {})
        return StrategyInputSchema(
            input_types=list(values.get("input_types", ["bar"])),
            symbols=list(values.get("symbols", [])),
            timeframes=values.get("timeframes"),
            warmup=values.get("warmup"),
            requires_position=bool(values.get("requires_position", True)),
            requires_portfolio=bool(values.get("requires_portfolio", False)),
            multi_asset=bool(values.get("multi_asset", False)),
            multi_timeframe=bool(values.get("multi_timeframe", False)),
        )


@dataclass(frozen=True)
class StrategySpecV2:
    name: str
    params: dict[str, Any]
    input_schema: StrategyInputSchema
    execution: dict[str, Any]
    recorder: dict[str, Any] | None = None

    @staticmethod
    def from_dict(values: dict) -> "StrategySpecV2":
        return StrategySpecV2(
            name=str(values["name"]),
            params=dict(values.get("params", {})),
            input_schema=StrategyInputSchema.from_dict(values.get("input_schema")),
            execution=dict(values.get("execution", {})),
            recorder=values.get("recorder"),
        )
