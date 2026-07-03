"""Turtle trading system configuration (user-facing parameters only).

Pure dataclass: no Nautilus, no pandas, no feature_engine / strategy_framework
imports. Ported from the TradeBlazer ``TurtleTrader`` system.

Sizing note
-----------
The framework generates the whole signal stream *before* any fills happen, so a
strategy cannot read live account equity while deciding. The Turtle unit size
therefore uses a **static** notional equity (``account_equity``) rather than the
running mark-to-market equity of the original ``Portfolio_CurrentCapital() +
Portfolio_UsedMargin()``. The N-based sizing formula is otherwise faithful::

    TurtleUnits = IntPart( account_equity * risk_ratio/100
                           / (N * contract_unit * big_point_value) )

with ``N`` = the previous bar's ATR (``XAverage(TrueRange, atr_length)``).
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TurtleTraderConfig:
    """User-facing parameters for the Turtle trading system."""

    # -- core Turtle parameters (map 1:1 to the TradeBlazer Params block) -----
    n_entries: int = 3                 # nEntries: max pyramided entries per position
    risk_ratio: float = 1.0            # RiskRatio: % risk per N (0-100)
    atr_length: int = 20               # ATRLength: N (ATR) averaging period
    breakout_len: int = 20             # boLength: short-period Donchian breakout
    failsafe_len: int = 55             # fsLength: long-period failsafe breakout
    trailing_exit_len: int = 10        # teLength: Donchian trailing-exit window
    last_profitable_trade_filter: bool = True  # LastProfitableTradeFilter

    # -- sizing inputs (see module docstring; static equity by design) --------
    account_equity: float = 100_000.0  # static notional equity for unit sizing
    contract_unit: float = 1.0         # ContractUnit()
    big_point_value: float = 1.0       # BigPointValue()
    min_point: float = 0.01            # MinMove*PriceScale (one tick)
    max_units_per_entry: float | None = None  # optional cap on TurtleUnits/entry

    # -- plumbing -------------------------------------------------------------
    instrument_id: str = "BTCUSDT.BINANCE"
    bar_type: str | None = None

    def __post_init__(self) -> None:
        if self.n_entries < 1:
            raise ValueError("n_entries must be >= 1.")
        if not (0 < self.risk_ratio <= 100):
            raise ValueError("risk_ratio must be in (0, 100].")
        for name in ("atr_length", "breakout_len", "failsafe_len", "trailing_exit_len"):
            if getattr(self, name) <= 0:
                raise ValueError(f"{name} must be > 0.")
        if self.account_equity <= 0:
            raise ValueError("account_equity must be > 0.")
        if self.contract_unit <= 0 or self.big_point_value <= 0:
            raise ValueError("contract_unit and big_point_value must be > 0.")
        if self.min_point < 0:
            raise ValueError("min_point must be >= 0.")
        if self.max_units_per_entry is not None and self.max_units_per_entry <= 0:
            raise ValueError("max_units_per_entry must be > 0 when set.")
