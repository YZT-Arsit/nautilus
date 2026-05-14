from nautilus_ext.instruments.instrument_profile import InstrumentProfile


def load_profiles() -> list[InstrumentProfile]:
    return [
        InstrumentProfile(
            symbol="ESM4",
            venue="XCME",
            instrument_type="futures_contract",
            instrument_id="ESM4.XCME",
            raw_symbol="ESM4",
            asset_class="index",
            exchange="XCME",
            underlying="ES",
            currency="USD",
            settlement_currency="USD",
            expiry="2024-06",
            price_precision=2,
            size_precision=0,
            price_increment="0.25",
            size_increment="1",
            multiplier="50",
            lot_size="1",
            source="registry:futures_contracts",
            confidence=0.4,
            metadata={
                "required_fields": [
                    "expiry",
                    "multiplier",
                    "price_increment",
                    "size_increment",
                    "underlying",
                    "settlement_currency",
                ],
            },
        ),
    ]
