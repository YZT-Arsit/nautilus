from nautilus_ext.instruments.instrument_profile import InstrumentProfile


def load_profiles() -> list[InstrumentProfile]:
    # Production should load CFD metadata from the company's official source.
    return [
        InstrumentProfile(
            symbol="US500",
            venue="SIM",
            instrument_type="cfd",
            instrument_id="US500.SIM",
            raw_symbol="US500",
            underlying="SPX",
            currency="USD",
            asset_class="index",
            price_precision=2,
            size_precision=1,
            price_increment="0.01",
            size_increment="0.1",
            source="registry:cfd",
            confidence=0.3,
            metadata={"note": "Example CFD profile; verify production metadata."},
        ),
    ]
