from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class WorkbookParametricConfig:
    source_registry_id: str = ""
    family: str = "sma_crossover"
    fast_window: int = 20
    slow_window: int = 60
    window: int = 20
    exit_window: int = 20
    filter_window: int = 55
    atr_window: int = 20
    multiplier: float = 1.5
    envelope_fraction: float = 0.02
    maximum_holding_bars: int = 0
    consecutive_bars: int = 1
    instrument_id: str = "BTCUSDT-PERP.BINANCE"
    bar_type: str | None = None

    def __post_init__(self) -> None:
        if self.family not in {"sma_crossover", "ma_envelope", "bollinger", "atr_channel"}:
            raise ValueError(f"unsupported exact workbook family: {self.family}")
        if min(self.fast_window, self.slow_window, self.window, self.exit_window, self.filter_window, self.atr_window) <= 0:
            raise ValueError("all windows must be positive")
        if self.family == "sma_crossover" and self.fast_window >= self.slow_window:
            raise ValueError("fast_window must be less than slow_window")
        if self.multiplier <= 0 or self.envelope_fraction <= 0 or self.consecutive_bars <= 0:
            raise ValueError("multipliers and counts must be positive")
