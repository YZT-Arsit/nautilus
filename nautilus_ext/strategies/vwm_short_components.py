from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class VwmShortSignalConfig:
    mom_len: int = 5
    avg_len: int = 20
    atr_len: int = 5
    atr_pcnt: float = 0.5
    setup_len: int = 5

    def __post_init__(self) -> None:
        if self.mom_len <= 0:
            raise ValueError("mom_len must be > 0.")
        if self.avg_len <= 0:
            raise ValueError("avg_len must be > 0.")
        if self.atr_len <= 0:
            raise ValueError("atr_len must be > 0.")
        if self.atr_pcnt < 0:
            raise ValueError("atr_pcnt must be >= 0.")
        if self.setup_len < 1:
            raise ValueError("setup_len must be >= 1.")


@dataclass(frozen=True)
class VwmShortSnapshot:
    prev_se_price: float | None
    prev_s_setup: int
    prev_bull_setup: bool
