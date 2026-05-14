from nautilus_ext.instruments.instrument_profile import InstrumentProfile


def load_profiles() -> list[InstrumentProfile]:
    return [
        InstrumentProfile(
            symbol="BTC-20240628-70000-C",
            venue="DERIBIT",
            instrument_type="crypto_option",
            instrument_id="BTC-20240628-70000-C.DERIBIT",
            raw_symbol="BTC-20240628-70000-C",
            base_currency="BTC",
            quote_currency="USD",
            settlement_currency="BTC",
            asset_class="crypto",
            underlying="BTC",
            expiry="2024-06-28",
            strike_price="70000",
            option_kind="CALL",
            source="registry:options",
            confidence=0.4,
            metadata={
                "required_fields": [
                    "underlying",
                    "expiry",
                    "strike_price",
                    "option_kind",
                ],
            },
        ),
    ]
