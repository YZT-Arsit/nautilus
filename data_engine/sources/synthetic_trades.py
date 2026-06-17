"""Synthetic trade source — a deterministic generated trade stream.

For unit tests and offline demos of the trade pipeline (no network). Produces a
deterministic alternating buy/sell trade sequence with a simple price path.
"""
from __future__ import annotations

from typing import Any

from data_engine.adapters.trade_adapter import BUY, SELL, make_trades
from data_engine.events import TradeEvent
from data_engine.time import ONE_SECOND_NS


def demo_trades(n: int) -> tuple[list[float], list[float], list[str]]:
    """A deterministic price/size/side path of ``n`` trades."""
    prices = [100.0 + (i % 5) for i in range(n)]
    sizes = [1.0 + (i % 3) for i in range(n)]
    sides = [BUY if i % 2 == 0 else SELL for i in range(n)]
    return prices, sizes, sides


class SyntheticTradeSource:
    """Generates deterministic trades; both warmup and stream are lists."""

    def __init__(
        self,
        instrument_id: str = "BTC/USDT",
        n_trades: int = 100,
        warmup: int = 0,
        step_ns: int = ONE_SECOND_NS,
    ) -> None:
        self._instrument_id = instrument_id
        self._n = n_trades
        self._warmup = warmup
        self._step_ns = step_ns

    def _all(self) -> list[TradeEvent]:
        prices, sizes, sides = demo_trades(self._n)
        return make_trades(
            prices, sizes, sides=sides,
            instrument_id=self._instrument_id, step_ns=self._step_ns,
        )

    def warmup(self) -> list[TradeEvent]:
        return self._all()[: self._warmup]

    def stream(self) -> list[TradeEvent]:
        return self._all()[self._warmup:]


def load_synthetic_trades(data_config: dict[str, Any]) -> tuple[list[TradeEvent], list[TradeEvent]]:
    """Build a :class:`SyntheticTradeSource` from a config and return ``(warmup, live)``."""
    source = SyntheticTradeSource(
        instrument_id=data_config.get("instrument_id", "BTC/USDT"),
        n_trades=int(data_config.get("n_trades", 100)),
        warmup=int(data_config.get("warmup", data_config.get("warmup_trades", 0))),
    )
    return source.warmup(), source.stream()
