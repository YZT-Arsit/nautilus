from nautilus_ext.instruments.instrument_profile import InstrumentProfile


def load_profiles() -> list[InstrumentProfile]:
    return [
        InstrumentProfile(
            symbol="EUR/USD",
            venue="SIM",
            instrument_type="currency_pair",
            instrument_id="EUR/USD.SIM",
            raw_symbol="EUR/USD",
            base_currency="EUR",
            quote_currency="USD",
            asset_class="fx",
            price_precision=5,
            size_precision=0,
            price_increment="0.00001",
            size_increment="1",
            source="registry:currency_pairs",
            confidence=0.6,
            metadata={"note": "Example FX profile; verify production metadata before use."},
        ),
        InstrumentProfile(
            symbol="BTCUSDT",
            venue="BINANCE",
            instrument_type="currency_pair",
            instrument_id="BTCUSDT.BINANCE",
            raw_symbol="BTCUSDT",
            base_currency="BTC",
            quote_currency="USDT",
            asset_class="crypto",
            source="registry:currency_pairs",
            confidence=0.5,
            metadata={"note": "Spot example profile; factory support may require more fields."},
        ),
    ]
