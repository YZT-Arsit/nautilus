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

Fees: the config ``fee_rate`` is applied by rebuilding the instrument with
``maker_fee = taker_fee = fee_rate`` (see :func:`_instrument_with_fees`).
``slippage_bps`` is **not yet wired** into the fill price - it is accepted but a
no-op at this stage (follow-up task).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from datetime import timezone
from decimal import Decimal
from typing import Any

from strategy_framework.execution.reports import FillRecord

# instrument_id -> TestInstrumentProvider factory method. MVP supports the
# liquid Binance spot pairs the bundled test kit ships; extend as needed.
_INSTRUMENT_FACTORIES = {
    "BTCUSDT.BINANCE": "btcusdt_binance",
    "ETHUSDT.BINANCE": "ethusdt_binance",
    "BTCUSDT-PERP.BINANCE": "btcusdt_perp_binance",
    "ETHUSDT-PERP.BINANCE": "ethusdt_perp_binance",
}
_CFFEX_MULTIPLIERS = {
    "IF": 300,
    "IH": 300,
    "IC": 200,
    "IM": 200,
}
_CFFEX_TICK_SIZE = "0.2"
_CFFEX_LOT_SIZE = 1
_CFFEX_CURRENCY = "CNY"
_CRYPTO_PERP_MVP_SPECS = {
    "SOLUSDT-PERP.BINANCE": {
        "symbol": "SOLUSDT-PERP",
        "raw_symbol": "SOLUSDT",
        "base": "SOL",
        "tick_size": "0.001",
        "price_precision": 3,
        "lot_size": "0.1",
        "size_precision": 1,
        "max_quantity": "100000.0",
        "max_price": "10000.000",
        "min_price": "0.001",
    },
    "BNBUSDT-PERP.BINANCE": {
        "symbol": "BNBUSDT-PERP",
        "raw_symbol": "BNBUSDT",
        "base": "BNB",
        "tick_size": "0.01",
        "price_precision": 2,
        "lot_size": "0.01",
        "size_precision": 2,
        "max_quantity": "100000.00",
        "max_price": "100000.00",
        "min_price": "0.01",
    },
}


@dataclass(frozen=True)
class InstrumentMapping:
    instrument_id: str
    kind: str
    factory: str | None = None
    venue: str | None = None
    symbol: str | None = None
    exchange: str | None = None
    asset_class: str | None = None
    tick_size: str | None = None
    price_precision: int | None = None
    lot_size: int | None = None
    multiplier: int | None = None
    currency: str | None = None
    underlying: str | None = None
    quote_asset: str | None = None
    settlement_asset: str | None = None
    margin_asset: str | None = None
    caveat: str | None = None
    metadata_source: str = "test_kit"


def _normalize_instrument_id(instrument_id: str, *, exchange: str | None = None) -> str:
    if "." in instrument_id:
        return instrument_id
    if exchange:
        return f"{instrument_id}.{exchange}"
    return instrument_id


def _cfffex_symbol_prefix(symbol: str) -> str:
    return symbol[:2].upper()


def _cffex_contract_expiration_ns(symbol: str) -> int:
    """Approximate index-futures expiration: third Friday of contract month UTC.

    This is an MVP deterministic mapping. It is sufficient for synthetic smoke
    tests and should be replaced by native ``futures_contract`` catalog metadata
    once that reader is wired into the backend.
    """
    yy = int(symbol[2:4])
    mm = int(symbol[4:6])
    year = 2000 + yy
    first = datetime(year, mm, 1, 7, 0, tzinfo=timezone.utc)
    friday_offset = (4 - first.weekday()) % 7
    third_friday = 1 + friday_offset + 14
    expiry = datetime(year, mm, third_friday, 7, 0, tzinfo=timezone.utc)
    return int(expiry.timestamp() * 1_000_000_000)


def resolve_instrument_mapping(
    instrument_id: str,
    *,
    exchange: str | None = None,
) -> InstrumentMapping:
    normalized = _normalize_instrument_id(instrument_id, exchange=exchange)
    factory = _INSTRUMENT_FACTORIES.get(normalized)
    if factory is not None:
        symbol, venue = normalized.split(".", 1)
        return InstrumentMapping(
            instrument_id=normalized,
            kind="test_kit_factory",
            factory=factory,
            venue=venue,
            symbol=symbol,
            exchange=venue,
            metadata_source="test_kit",
        )
    if "." not in normalized:
        raise NautilusUnavailableError(_unsupported_instrument_message(normalized))
    symbol, venue = normalized.split(".", 1)
    prefix = _cfffex_symbol_prefix(symbol)
    if venue == "CFFEX" and prefix in _CFFEX_MULTIPLIERS and len(symbol) == 6 and symbol[2:].isdigit():
        return InstrumentMapping(
            instrument_id=normalized,
            kind="cffex_futures_mvp",
            venue="CFFEX",
            symbol=symbol,
            exchange="CFFEX",
            asset_class="INDEX",
            tick_size=_CFFEX_TICK_SIZE,
            price_precision=1,
            lot_size=_CFFEX_LOT_SIZE,
            multiplier=_CFFEX_MULTIPLIERS[prefix],
            currency=_CFFEX_CURRENCY,
            underlying=prefix,
            metadata_source="deterministic_mvp",
        )
    spec = _CRYPTO_PERP_MVP_SPECS.get(normalized)
    if spec is not None:
        return InstrumentMapping(
            instrument_id=normalized,
            kind="crypto_perpetual_mvp",
            venue="BINANCE",
            symbol=spec["symbol"],
            exchange="BINANCE",
            asset_class="CRYPTO_PERPETUAL",
            tick_size=spec["tick_size"],
            price_precision=int(spec["price_precision"]),
            lot_size=1,
            currency="USDT",
            underlying=spec["base"],
            quote_asset="USDT",
            settlement_asset="USDT",
            margin_asset="USDT",
            caveat="deterministic MVP perpetual mapping; funding, margin, liquidation, and mark/index effects are not modeled",
            metadata_source="deterministic_mvp",
        )
    raise NautilusUnavailableError(_unsupported_instrument_message(normalized))


def _supported_instruments_summary() -> list[str]:
    return [*_INSTRUMENT_FACTORIES, *_CRYPTO_PERP_MVP_SPECS, "CFFEX IF/IH/IC/IM YYMM futures (MVP)"]


def _unsupported_instrument_message(instrument_id: str) -> str:
    return (
        f"native backtest has no instrument mapping for {instrument_id!r}. "
        f"Supported (MVP): {_supported_instruments_summary()}. Add a mapping in "
        "strategy_framework/backends/nautilus_native.py or wire futures_contract "
        "catalog metadata to extend coverage."
    )


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


def _instrument_with_fees(instrument, fee_rate: float):
    """Return a copy of ``instrument`` whose maker & taker fees equal ``fee_rate``.

    The bundled ``TestInstrumentProvider`` instruments carry their own default
    fees (BTCUSDT spot taker = 0.001), so the config ``fee_rate`` was previously
    ignored. We rebuild the instrument through its own ``to_dict``/``from_dict``
    (version-robust; no hand-listing of every field) overriding only the fee
    fields. Setting **both** maker and taker to ``fee_rate`` is intentional and
    correct: a single fill is charged maker **or** taker (never both), so every
    market (taker) fill then pays exactly ``fee_rate``.

    Returns the instrument unchanged if it exposes no maker/taker fee fields, and
    asserts the override actually took effect (fails loud rather than silently
    using the wrong fee).
    """
    cls = type(instrument)
    to_dict = getattr(cls, "to_dict", None) or getattr(instrument, "to_dict", None)
    from_dict = getattr(cls, "from_dict", None)
    if to_dict is None or from_dict is None:
        return instrument
    try:
        d = cls.to_dict(instrument)
    except TypeError:
        d = instrument.to_dict()
    if "maker_fee" not in d and "taker_fee" not in d:
        return instrument  # this instrument type has no fee fields to wire
    fee_str = format(float(fee_rate), "f")
    if "maker_fee" in d:
        d["maker_fee"] = fee_str
    if "taker_fee" in d:
        d["taker_fee"] = fee_str
    rebuilt = from_dict(d)
    # Guard: the override must have taken effect.
    eff = float(getattr(rebuilt, "taker_fee", fee_rate))
    if abs(eff - float(fee_rate)) > 1e-12:
        raise NautilusUnavailableError(
            f"failed to apply config fee_rate={fee_rate} to instrument "
            f"{getattr(instrument, 'id', '?')}: effective taker_fee={eff}"
        )
    return rebuilt


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
        AssetClass,
        OmsType,
        OrderSide,
    )
    from nautilus_trader.model.identifiers import InstrumentId, Symbol, TraderId, Venue  # noqa: PLC0415
    from nautilus_trader.model.instruments import CryptoPerpetual, FuturesContract  # noqa: PLC0415
    from nautilus_trader.model.objects import Currency, Money, Price, Quantity  # noqa: PLC0415
    from nautilus_trader.persistence.wranglers import BarDataWrangler  # noqa: PLC0415
    from nautilus_trader.test_kit.providers import TestInstrumentProvider  # noqa: PLC0415
    from nautilus_trader.trading.strategy import Strategy  # noqa: PLC0415

    mapping = resolve_instrument_mapping(instrument_id)
    if mapping.kind == "test_kit_factory":
        instrument = getattr(TestInstrumentProvider, mapping.factory)()
    elif mapping.kind == "cffex_futures_mvp":
        activation_ns = 0
        expiration_ns = _cffex_contract_expiration_ns(mapping.symbol)
        instrument = FuturesContract(
            instrument_id=InstrumentId(symbol=Symbol(mapping.symbol), venue=Venue(mapping.venue)),
            raw_symbol=Symbol(mapping.symbol),
            asset_class=AssetClass.INDEX,
            exchange=mapping.exchange,
            currency=Currency.from_str(mapping.currency),
            price_precision=mapping.price_precision,
            price_increment=Price.from_str(mapping.tick_size),
            multiplier=Quantity.from_int(mapping.multiplier),
            lot_size=Quantity.from_int(mapping.lot_size),
            underlying=mapping.underlying,
            activation_ns=activation_ns,
            expiration_ns=expiration_ns,
            ts_event=activation_ns,
            ts_init=activation_ns,
        )
    elif mapping.kind == "crypto_perpetual_mvp":
        spec = _CRYPTO_PERP_MVP_SPECS[mapping.instrument_id]
        ts_ns = 0
        instrument = CryptoPerpetual(
            instrument_id=InstrumentId(symbol=Symbol(spec["symbol"]), venue=Venue("BINANCE")),
            raw_symbol=Symbol(spec["raw_symbol"]),
            base_currency=Currency.from_str(spec["base"]),
            quote_currency=Currency.from_str("USDT"),
            settlement_currency=Currency.from_str("USDT"),
            is_inverse=False,
            price_precision=int(spec["price_precision"]),
            price_increment=Price.from_str(spec["tick_size"]),
            size_precision=int(spec["size_precision"]),
            size_increment=Quantity.from_str(spec["lot_size"]),
            max_quantity=Quantity.from_str(spec["max_quantity"]),
            min_quantity=Quantity.from_str(spec["lot_size"]),
            max_notional=None,
            min_notional=Money(10.00, Currency.from_str("USDT")),
            max_price=Price.from_str(spec["max_price"]),
            min_price=Price.from_str(spec["min_price"]),
            margin_init=Decimal("1.00"),
            margin_maint=Decimal("0.35"),
            maker_fee=Decimal("0.0002"),
            taker_fee=Decimal("0.0004"),
            ts_event=ts_ns,
            ts_init=ts_ns,
        )
    else:  # pragma: no cover - resolver guards this
        raise NautilusUnavailableError(_unsupported_instrument_message(instrument_id))
    # Wire the config fee_rate into the instrument's maker/taker fee so the
    # backtest charges the configured rate instead of the test instrument's
    # built-in default. slippage_bps is NOT yet wired into the fill price - it
    # remains a no-op this stage (tracked as a follow-up; see module note).
    instrument = _instrument_with_fees(instrument, fee_rate)
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
            # bypass=True: skip Nautilus's global (Rust) logger init. Required so
            # multiple native backtests can run in ONE process (fee_scenarios,
            # native batch) — re-initializing the logger otherwise panics.
            logging=LoggingConfig(bypass_logging=True),
        )
    )
    quote_currency = getattr(instrument, "quote_currency", None) or getattr(instrument, "currency", None)
    engine.add_venue(
        venue=venue,
        oms_type=OmsType.NETTING,
        account_type=account_type,
        starting_balances=[Money(initial_cash, quote_currency)],
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
        "instrument_mapping": {
            "kind": mapping.kind,
            "metadata_source": mapping.metadata_source,
            "multiplier": mapping.multiplier,
            "tick_size": mapping.tick_size,
            "lot_size": mapping.lot_size,
        },
    }
    try:
        engine.run()
        account = engine.portfolio.account(venue)
        if account is not None:
            quote = quote_currency
            try:
                summary["final_balance_quote"] = _to_float(account.balance_total(quote))
                summary["quote_currency"] = quote.code
            except Exception as exc:  # pragma: no cover - defensive
                summary["balance_error"] = str(exc)
        summary["fills_captured"] = len(captured)
    finally:
        engine.dispose()

    return captured, summary
