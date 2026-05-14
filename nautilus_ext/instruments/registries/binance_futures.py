from nautilus_ext.instruments.instrument_profile import InstrumentProfile


def load_profiles() -> list[InstrumentProfile]:
    # These specs are internal backtest defaults and should be replaced by the
    # company's official instrument metadata source for production.
    return [
        _profile("BCHUSDT", "BCH", "USDT", price_precision=2, size_precision=3),
        _profile("BTCUSDT", "BTC", "USDT", price_precision=1, size_precision=3),
        _profile("ETHUSDT", "ETH", "USDT", price_precision=2, size_precision=3),
    ]


def _profile(
    symbol: str,
    base_currency: str,
    quote_currency: str,
    price_precision: int,
    size_precision: int,
) -> InstrumentProfile:
    return InstrumentProfile(
        symbol=symbol,
        venue="BINANCE",
        instrument_type="crypto_perpetual",
        instrument_id=f"{symbol}-PERP.BINANCE",
        raw_symbol=symbol,
        base_currency=base_currency,
        quote_currency=quote_currency,
        settlement_currency=quote_currency,
        asset_class="crypto",
        exchange="BINANCE",
        exchange_symbol=symbol,
        price_precision=price_precision,
        size_precision=size_precision,
        price_increment="0.01" if price_precision == 2 else "0.1",
        size_increment="0.001",
        maker_fee="0.0002",
        taker_fee="0.0004",
        margin_init="0",
        margin_maint="0",
        multiplier="1",
        lot_size="1",
        settlement_type="linear",
        is_inverse=False,
        source="registry:binance_futures",
        confidence=0.95,
        metadata={"note": "Internal default profile; replace with official metadata."},
    )
