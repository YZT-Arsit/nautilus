from nautilus_ext.instruments.instrument_profile import InstrumentProfile


def load_profiles() -> list[InstrumentProfile]:
    return [
        InstrumentProfile(
            symbol="BTC_ETH_SPREAD",
            venue="INTERNAL",
            instrument_type="synthetic",
            instrument_id="BTC_ETH_SPREAD.INTERNAL",
            raw_symbol="BTC_ETH_SPREAD",
            synthetic_formula="BTCUSDT.BINANCE - ETHUSDT.BINANCE",
            components=["BTCUSDT.BINANCE", "ETHUSDT.BINANCE"],
            price_precision=2,
            price_increment="0.01",
            source="registry:synthetics",
            confidence=0.3,
            metadata={"note": "Example synthetic profile; construction not implemented."},
        ),
    ]
