from nautilus_ext.instruments.instrument_profile import InstrumentProfile


def load_profiles() -> list[InstrumentProfile]:
    return [
        InstrumentProfile(
            symbol="XAU/USD",
            venue="SIM",
            instrument_type="commodity",
            instrument_id="XAU/USD.SIM",
            raw_symbol="XAU/USD",
            currency="USD",
            asset_class="commodity",
            underlying="XAU",
            price_precision=2,
            size_precision=3,
            price_increment="0.01",
            size_increment="0.001",
            source="registry:commodities",
            confidence=0.3,
            metadata={"note": "Example commodity profile; verify production metadata."},
        ),
    ]
