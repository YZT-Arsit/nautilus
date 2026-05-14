import inspect

from nautilus_ext.instruments.instrument_profile import InstrumentProfile


class NautilusInstrumentFactory:
    _CLASS_BY_TYPE = {
        "crypto_perpetual": ("CryptoPerpetual", "PerpetualContract"),
        "perpetual_contract": ("PerpetualContract",),
        "currency_pair": ("CurrencyPair",),
        "equity": ("Equity",),
        "futures_contract": ("FuturesContract",),
        "crypto_future": ("CryptoFuture",),
        "option_contract": ("OptionContract",),
        "crypto_option": ("CryptoOption",),
        "cfd": ("Cfd",),
        "commodity": ("Commodity",),
        "index": ("IndexInstrument",),
        "futures_spread": ("FuturesSpread",),
        "option_spread": ("OptionSpread",),
        "binary_option": ("BinaryOption",),
        "betting": ("BettingInstrument",),
        "synthetic": ("SyntheticInstrument",),
    }

    _REQUIRED_FIELD_HINTS = {
        "crypto_perpetual": [
            "instrument_id",
            "raw_symbol",
            "base_currency",
            "quote_currency",
            "settlement_currency",
            "price_precision",
            "size_precision",
            "price_increment",
            "size_increment",
            "maker_fee",
            "taker_fee",
        ],
        "currency_pair": [
            "instrument_id",
            "raw_symbol",
            "base_currency",
            "quote_currency",
            "price_precision",
            "size_precision",
            "price_increment",
            "size_increment",
        ],
        "equity": ["instrument_id", "raw_symbol", "currency", "price_increment", "lot_size"],
        "futures_contract": [
            "expiry",
            "multiplier",
            "price_increment",
            "size_increment",
            "underlying",
            "settlement_currency",
        ],
        "option_contract": ["underlying", "expiry", "strike_price", "option_kind"],
    }

    @staticmethod
    def build(profile: InstrumentProfile):
        class_names = NautilusInstrumentFactory._CLASS_BY_TYPE.get(profile.instrument_type)
        if not class_names:
            raise NotImplementedError(
                f"Instrument type {profile.instrument_type!r} is not mapped to a Nautilus class. "
                f"Profile: {profile.to_dict()}"
            )

        cls = NautilusInstrumentFactory._resolve_first_available_class(class_names, profile)
        signature = NautilusInstrumentFactory._signature_for(cls)
        raise NotImplementedError(
            "Automatic Nautilus instrument construction is not yet safely implemented for "
            f"instrument_type={profile.instrument_type!r}, selected_class={cls.__name__!r}. "
            f"Constructor signature: {signature}. "
            f"Profile: {profile.to_dict()}. "
            "Needed fields: "
            f"{NautilusInstrumentFactory._REQUIRED_FIELD_HINTS.get(profile.instrument_type, [])}. "
            "Add an explicit constructor adapter for this class before production use."
        )

    @staticmethod
    def _resolve_first_available_class(class_names: tuple[str, ...], profile: InstrumentProfile):
        try:
            import nautilus_trader.model.instruments as instruments
        except Exception as exc:
            raise NotImplementedError(
                "Unable to import Nautilus instrument classes in the current environment. "
                f"instrument_type={profile.instrument_type!r}, requested_classes={class_names}, "
                f"profile={profile.to_dict()}, original_error={exc}"
            ) from exc

        for class_name in class_names:
            cls = getattr(instruments, class_name, None)
            if cls is not None:
                return cls

        raise NotImplementedError(
            f"No Nautilus instrument class found for instrument_type={profile.instrument_type!r}. "
            f"Tried classes: {class_names}. Profile: {profile.to_dict()}"
        )

    @staticmethod
    def _signature_for(cls) -> str:
        try:
            return str(inspect.signature(cls))
        except Exception as exc:
            return f"<signature unavailable: {exc}>"
