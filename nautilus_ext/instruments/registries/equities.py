from nautilus_ext.instruments.instrument_profile import InstrumentProfile


def load_profiles() -> list[InstrumentProfile]:
    return [
        InstrumentProfile(
            symbol="AAPL",
            venue="NASDAQ",
            instrument_type="equity",
            instrument_id="AAPL.NASDAQ",
            raw_symbol="AAPL",
            asset_class="equity",
            exchange="NASDAQ",
            currency="USD",
            price_precision=2,
            size_precision=0,
            price_increment="0.01",
            size_increment="1",
            lot_size="1",
            source="registry:equities",
            confidence=0.4,
            metadata={
                "required_fields": [
                    "raw_symbol",
                    "venue",
                    "currency",
                    "price_increment",
                    "lot_size",
                ],
            },
        ),
    ]
