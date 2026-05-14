from nautilus_ext.instruments.instrument_profile import InstrumentProfile
from nautilus_ext.instruments.metadata_requirements import missing_fields_for_profile


class NautilusInstrumentFactory:
    _ADAPTERS = {
        "crypto_perpetual": "build_crypto_perpetual_from_profile",
        "currency_pair": "build_currency_pair_from_profile",
        "equity": "build_equity_from_profile",
        "futures_contract": "build_futures_contract_from_profile",
        "crypto_future": "build_crypto_future_from_profile",
        "option_contract": "build_option_contract_from_profile",
        "crypto_option": "build_crypto_option_from_profile",
        "cfd": "build_cfd_from_profile",
        "commodity": "build_commodity_from_profile",
        "index": "build_index_from_profile",
        "synthetic": "build_synthetic_from_profile",
    }
    _NOT_IMPLEMENTED_TYPES = {
        "futures_spread",
        "option_spread",
        "binary_option",
        "betting",
        "perpetual_contract",
        "unknown",
    }

    @staticmethod
    def build(profile: InstrumentProfile):
        if profile.instrument_type in NautilusInstrumentFactory._NOT_IMPLEMENTED_TYPES:
            raise NotImplementedError(
                f"Instrument construction is not implemented for "
                f"instrument_type={profile.instrument_type!r}. "
                f"missing_fields={missing_fields_for_profile(profile)}, "
                f"profile={profile.to_dict()}"
            )

        adapter_name = NautilusInstrumentFactory._ADAPTERS.get(profile.instrument_type)
        if adapter_name is None:
            raise NotImplementedError(
                f"Instrument type {profile.instrument_type!r} is not mapped to an adapter. "
                f"Profile: {profile.to_dict()}"
            )

        if profile.instrument_type != "crypto_perpetual":
            missing = missing_fields_for_profile(profile)
            if missing:
                raise NotImplementedError(
                    f"Missing required metadata for instrument_type={profile.instrument_type!r}: "
                    f"{missing}. profile={profile.to_dict()}"
                )

        import nautilus_ext.instruments.constructor_adapters as adapters

        adapter = getattr(adapters, adapter_name)
        return adapter(profile)
