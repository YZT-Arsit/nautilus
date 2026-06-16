"""Native Nautilus ``BacktestEngine`` adapter (the real engine path).

This is the **only** place in the execution stack that imports
``nautilus_trader``, and it does so **lazily** (inside :func:`run_native_backtest`)
so importing this module never requires Nautilus to be installed/compiled.

Responsibility - translate at the boundary, nothing more:

* internal bar marks (``data_engine.BarEvent`` dicts)  -> Nautilus ``Bar`` data;
* internal ``OrderIntent`` / ``PositionIntent`` (pre-computed upstream by the
  feature engine + strategy + :class:`SignalToOrderPolicy`)  -> Nautilus market
  orders submitted by a thin replay ``Strategy``;
* Nautilus fills (``OrderFilled`` events)  -> internal :class:`FillRecord` list.

It does **not** compute features, hold strategy logic, or read data files. The
replay strategy only re-emits decisions already made outside Nautilus, keyed by
bar timestamp - so the architectural rule "no feature maths inside Nautilus"
holds.
"""
from __future__ import annotations

from typing import Any

from strategy_framework.execution.reports import FillRecord

# instrument_id -> TestInstrumentProvider factory method. MVP supports the
# liquid Binance spot pairs the bundled test kit ships; extend as needed.
_INSTRUMENT_FACTORIES = {
    "BTCUSDT.BINANCE": "btcusdt_binance",
    "ETHUSDT.BINANCE": "ethusdt_binance",
}


class NautilusUnavailableError(RuntimeError):
    """Raised when ``mode='nautilus_native'`` is requested but Nautilus is absent.

    This is a real dependency/error condition - deliberately **not** a placeholder
    ``NotImplementedError``. The native path is implemented; it simply needs a
    working ``nautilus_trader`` install (present on the backtest server).
    """


def nautilus_available() -> bool:
    try:
        import nautilus_trader  # noqa: F401, PLC0415

        return True
    except Exception:
        return False


def _to_float(value: Any) -> float:
    if value is None:
        return 0.0
    for attr in ("as_double",):
        fn = getattr(value, attr, None)
        if callable(fn):
            try:
                return float(fn())
            except Exception:
                pass
    try:
        return float(value)
    except Exception:
        return 0.0


def run_native_backtest(
    *,
    bars: list[dict[str, Any]],
    intents_by_ts: dict[int, tuple[str, float]],
    instrument_id: str,
    quantity: float,
    initial_cash: float,
    allow_short: bool = False,
    fee_rate: float = 0.0,
    slippage_bps: float = 0.0,
    log_level: str = "ERROR",
    trader_id: str = "BACKTEST-NATIVE-001",
) -> tuple[list[FillRecord], dict[str, Any]]:
    """Run a native Nautilus ``BacktestEngine`` and return ``(fills, summary)``.

    ``bars`` rows carry ``event_time_ns, open, high, low, close, volume``.
    ``intents_by_ts`` maps a bar timestamp (ns) to ``(action, quantity)`` where
    action is ``"BUY"`` / ``"SELL"`` / ``"FLAT"``. The replay strategy submits a
    market order for each, letting the Nautilus matching engine produce fills.
    """
    if not nautilus_available():
        raise NautilusUnavailableError(
            "mode='nautilus_native' requires the 'nautilus_trader' package, which is "
            "not importable in this environment. Install/compile nautilus_trader (it is "
            "available on the backtest server) or use mode='simulated' for a "
            "dependency-free fill/PnL report."
        )
    if not bars:
        return [], {"note": "no bars to run", "engine": "BacktestEngine"}

    # -- lazy Nautilus imports (kept inside the function on purpose) ----------
    import pandas as pd  # noqa: PLC0415
    from nautilus_trader.backtest.engine import (  # noqa: PLC0415
        BacktestEngine,
        BacktestEngineConfig,
    )
    from nautilus_trader.config import LoggingConfig  # noqa: PLC0415
    from nautilus_trader.model.data import BarType  # noqa: PLC0415
    from nautilus_trader.model.enums import (  # noqa: PLC0415
        AccountType,
        OmsType,
        OrderSide,
    )
    from nautilus_trader.model.identifiers import TraderId  # noqa: PLC0415
    from nautilus_trader.model.objects import Money  # noqa: PLC0415
    from nautilus_trader.persistence.wranglers import BarDataWrangler  # noqa: PLC0415
    from nautilus_trader.test_kit.providers import TestInstrumentProvider  # noqa: PLC0415
    from nautilus_trader.trading.strategy import Strategy  # noqa: PLC0415

    factory = _INSTRUMENT_FACTORIES.get(instrument_id)
    if factory is None:
        raise NautilusUnavailableError(
            f"native backtest has no instrument mapping for {instrument_id!r}. "
            f"Supported (MVP): {sorted(_INSTRUMENT_FACTORIES)}. Add a mapping in "
            f"strategy_framework/backends/nautilus_native.py to extend coverage."
        )
    instrument = getattr(TestInstrumentProvider, factory)()
    venue = instrument.id.venue
    bar_type = BarType.from_str(f"{instrument.id}-1-MINUTE-LAST-EXTERNAL")

    # internal bar dicts -> DataFrame the wrangler understands (UTC DatetimeIndex
    # named 'timestamp'; OHLCV columns) -> Nautilus Bar objects.
    index = pd.to_datetime([b["event_time_ns"] for b in bars], unit="ns", utc=True)
    frame = pd.DataFrame(
        {
            "open": [float(b.get("open", b["close"])) for b in bars],
            "high": [float(b.get("high", b["close"])) for b in bars],
            "low": [float(b.get("low", b["close"])) for b in bars],
            "close": [float(b["close"]) for b in bars],
            "volume": [float(b.get("volume", 0.0)) for b in bars],
        },
        index=index,
    )
    frame.index.name = "timestamp"
    frame = frame[~frame.index.duplicated(keep="last")].sort_index()
    nautilus_bars = BarDataWrangler(bar_type, instrument).process(frame)

    captured: list[FillRecord] = []

    class _IntentReplayStrategy(Strategy):
        """Re-emit pre-computed intents as market orders; capture fills."""

        def __init__(self) -> None:
            super().__init__()
            self._instrument = None

        def on_start(self) -> None:
            self._instrument = self.cache.instrument(instrument.id)
            self.subscribe_bars(bar_type)

        def on_stop(self) -> None:
            self.cancel_all_orders(instrument.id)
            self.unsubscribe_bars(bar_type)

        def _net_qty(self) -> float:
            net = 0.0
            for pos in self.cache.positions_open(instrument_id=instrument.id):
                net += float(pos.signed_qty)
            return net

        def _submit(self, side, qty: float, reduce_only: bool = False) -> None:
            if qty <= 0 or self._instrument is None:
                return
            order = self.order_factory.market(
                instrument_id=instrument.id,
                order_side=side,
                quantity=self._instrument.make_qty(qty),
                reduce_only=reduce_only,
            )
            self.submit_order(order)

        def on_bar(self, bar) -> None:
            action = intents_by_ts.get(int(bar.ts_event))
            if action is None:
                return
            kind, qty = action
            if kind == "BUY":
                self._submit(OrderSide.BUY, qty)
            elif kind == "SELL":  # explicit short (margin accounts only)
                self._submit(OrderSide.SELL, qty)
            elif kind == "FLAT":
                net = self._net_qty()
                if net > 0:
                    self._submit(OrderSide.SELL, net, reduce_only=True)
                elif net < 0:
                    self._submit(OrderSide.BUY, -net, reduce_only=True)

        def on_order_filled(self, event) -> None:
            side = "BUY" if event.order_side == OrderSide.BUY else "SELL"
            captured.append(
                FillRecord(
                    instrument_id=str(event.instrument_id),
                    side=side,
                    quantity=_to_float(event.last_qty),
                    price=_to_float(event.last_px),
                    event_time_ns=int(event.ts_event),
                    source="nautilus",
                    metadata={"commission": _to_float(getattr(event, "commission", None))},
                )
            )

    account_type = AccountType.MARGIN if allow_short else AccountType.CASH
    engine = BacktestEngine(
        config=BacktestEngineConfig(
            trader_id=TraderId(trader_id),
            logging=LoggingConfig(log_level=log_level),
        )
    )
    engine.add_venue(
        venue=venue,
        oms_type=OmsType.NETTING,
        account_type=account_type,
        starting_balances=[Money(initial_cash, instrument.quote_currency)],
        base_currency=None,
    )
    engine.add_instrument(instrument)
    engine.add_data(nautilus_bars)
    engine.add_strategy(_IntentReplayStrategy())

    summary: dict[str, Any] = {
        "engine": "BacktestEngine",
        "instrument_id": str(instrument.id),
        "account_type": account_type.name,
        "bars_loaded": len(nautilus_bars),
    }
    try:
        engine.run()
        account = engine.portfolio.account(venue)
        if account is not None:
            quote = instrument.quote_currency
            try:
                summary["final_balance_quote"] = _to_float(account.balance_total(quote))
                summary["quote_currency"] = quote.code
            except Exception as exc:  # pragma: no cover - defensive
                summary["balance_error"] = str(exc)
        summary["fills_captured"] = len(captured)
    finally:
        engine.dispose()

    return captured, summary
