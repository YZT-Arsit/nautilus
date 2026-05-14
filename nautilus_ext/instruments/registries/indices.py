from nautilus_ext.instruments.instrument_profile import InstrumentProfile


def load_profiles() -> list[InstrumentProfile]:
    return [
        InstrumentProfile(
            symbol="SPX",
            venue="CBOE",
            instrument_type="index",
            instrument_id="SPX.CBOE",
            raw_symbol="SPX",
            currency="USD",
            asset_class="index",
            price_precision=2,
            price_increment="0.01",
            source="registry:indices",
            confidence=0.3,
            metadata={"note": "Example index profile; verify production metadata."},
        ),
    ]
