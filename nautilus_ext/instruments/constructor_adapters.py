from __future__ import annotations

import inspect
from typing import Any

from nautilus_ext.instruments.instrument_profile import InstrumentProfile
from nautilus_ext.instruments.metadata_requirements import missing_fields_for_profile


def build_crypto_perpetual_from_profile(profile: InstrumentProfile):
    try:
        from nautilus_trader.model.instruments import CryptoPerpetual
    except Exception as exc:
        raise NotImplementedError(
            "Unable to import Nautilus CryptoPerpetual in the current environment. "
            f"profile={profile.to_dict()}, original_error={exc!r}"
        ) from exc

    errors = []
    for values in candidate_crypto_perpetual_dicts(profile):
        try:
            return CryptoPerpetual.from_dict(values)
        except Exception as exc:
            errors.append(
                {
                    "keys": list(values.keys()),
                    "values": values,
                    "error": repr(exc),
                }
            )

    error_lines = [
        "Unable to construct CryptoPerpetual from InstrumentProfile using "
        "CryptoPerpetual.from_dict(values).",
        f"profile={profile.to_dict()}",
        "candidate_errors:",
    ]
    for index, error in enumerate(errors, start=1):
        error_lines.append(
            f"{index}. keys={error['keys']}, error={error['error']}, values={error['values']}"
        )
    error_lines.append(
        "See crypto_perpetual_help.txt or Nautilus tests/unit_tests/model/test_instrument.py "
        "for CryptoPerpetual.to_dict()/from_dict() examples."
    )
    raise NotImplementedError("\n".join(error_lines))


def candidate_crypto_perpetual_dicts(profile: InstrumentProfile) -> list[dict[str, Any]]:
    base = {
        "type": "CryptoPerpetual",
        "id": profile.instrument_id,
        "raw_symbol": profile.raw_symbol,
        "base_currency": profile.base_currency,
        "quote_currency": profile.quote_currency,
        "settlement_currency": profile.settlement_currency,
        "is_inverse": bool(profile.is_inverse) if profile.is_inverse is not None else False,
        "price_precision": profile.price_precision,
        "price_increment": profile.price_increment,
        "size_precision": profile.size_precision,
        "size_increment": profile.size_increment,
        "multiplier": profile.multiplier or "1",
        "lot_size": "1",
        "max_quantity": None,
        "min_quantity": profile.size_increment,
        "max_notional": None,
        "min_notional": None,
        "max_price": None,
        "min_price": profile.price_increment,
        "margin_init": profile.margin_init or "0",
        "margin_maint": profile.margin_maint or "0",
        "maker_fee": profile.maker_fee or "0",
        "taker_fee": profile.taker_fee or "0",
        "ts_event": profile.activation_ns or 0,
        "ts_init": profile.activation_ns or 0,
        "info": profile.metadata or {},
        "tick_scheme_name": None,
    }

    instrument_id_variant = dict(base)
    instrument_id_variant["instrument_id"] = instrument_id_variant.pop("id")

    no_type_variant = dict(base)
    no_type_variant.pop("type", None)

    symbol_variant = dict(base)
    symbol_variant["symbol"] = profile.raw_symbol

    minimal_snake_case = {
        "type": "CryptoPerpetual",
        "id": profile.instrument_id,
        "raw_symbol": profile.raw_symbol,
        "base_currency": profile.base_currency,
        "quote_currency": profile.quote_currency,
        "settlement_currency": profile.settlement_currency,
        "is_inverse": bool(profile.is_inverse) if profile.is_inverse is not None else False,
        "price_precision": profile.price_precision,
        "size_precision": profile.size_precision,
        "price_increment": profile.price_increment,
        "size_increment": profile.size_increment,
        "maker_fee": profile.maker_fee,
        "taker_fee": profile.taker_fee,
        "margin_init": profile.margin_init,
        "margin_maint": profile.margin_maint,
        "info": profile.metadata or {},
    }

    return [
        base,
        instrument_id_variant,
        no_type_variant,
        symbol_variant,
        minimal_snake_case,
    ]


def build_currency_pair_from_profile(profile: InstrumentProfile):
    return _build_from_profile("CurrencyPair", profile, _basic_candidates("CurrencyPair", profile))


def build_equity_from_profile(profile: InstrumentProfile):
    return _build_from_profile("Equity", profile, _basic_candidates("Equity", profile))


def build_futures_contract_from_profile(profile: InstrumentProfile):
    return _build_from_profile(
        "FuturesContract",
        profile,
        _basic_candidates("FuturesContract", profile),
    )


def build_crypto_future_from_profile(profile: InstrumentProfile):
    return _build_from_profile("CryptoFuture", profile, _basic_candidates("CryptoFuture", profile))


def build_option_contract_from_profile(profile: InstrumentProfile):
    return _build_from_profile("OptionContract", profile, _basic_candidates("OptionContract", profile))


def build_crypto_option_from_profile(profile: InstrumentProfile):
    return _build_from_profile("CryptoOption", profile, _basic_candidates("CryptoOption", profile))


def build_cfd_from_profile(profile: InstrumentProfile):
    return _build_from_profile("Cfd", profile, _basic_candidates("Cfd", profile))


def build_commodity_from_profile(profile: InstrumentProfile):
    return _build_from_profile("Commodity", profile, _basic_candidates("Commodity", profile))


def build_index_from_profile(profile: InstrumentProfile):
    return _build_from_profile("IndexInstrument", profile, _basic_candidates("IndexInstrument", profile))


def build_synthetic_from_profile(profile: InstrumentProfile):
    raise NotImplementedError(
        "SyntheticInstrument construction is not implemented. "
        f"profile={profile.to_dict()}, missing_fields={missing_fields_for_profile(profile)}"
    )


def try_from_dict_candidates(cls, candidates: list[dict], profile: InstrumentProfile):
    errors = []
    for values in candidates:
        try:
            return cls.from_dict(values)
        except Exception as exc:
            errors.append(
                {
                    "keys": list(values.keys()),
                    "values": values,
                    "error": repr(exc),
                }
            )
    raise _adapter_error(cls, profile, errors)


def describe_constructor_support(cls) -> dict:
    return {
        "class_name": cls.__name__,
        "has_from_dict": hasattr(cls, "from_dict"),
        "has_to_dict": hasattr(cls, "to_dict"),
        "signature": _signature_for(cls),
        "from_dict_signature": _signature_for(getattr(cls, "from_dict", None)),
    }


def _build_from_profile(class_name: str, profile: InstrumentProfile, candidates: list[dict]):
    missing = missing_fields_for_profile(profile)
    if missing:
        raise NotImplementedError(
            f"Missing required metadata for instrument_type={profile.instrument_type!r}, "
            f"selected_class={class_name!r}: {missing}. profile={profile.to_dict()}"
        )

    cls = _resolve_instrument_class(class_name, profile)
    if not hasattr(cls, "from_dict"):
        raise NotImplementedError(
            f"selected_class={class_name!r} has no from_dict support. "
            f"constructor_support={describe_constructor_support(cls)}, profile={profile.to_dict()}"
        )
    return try_from_dict_candidates(cls, candidates, profile)


def _resolve_instrument_class(class_name: str, profile: InstrumentProfile):
    try:
        import nautilus_trader.model.instruments as instruments
    except Exception as exc:
        raise NotImplementedError(
            "Unable to import Nautilus instrument classes in the current environment. "
            f"selected_class={class_name!r}, profile={profile.to_dict()}, original_error={exc!r}"
        ) from exc

    cls = getattr(instruments, class_name, None)
    if cls is None:
        raise NotImplementedError(
            f"Nautilus instrument class {class_name!r} is unavailable. profile={profile.to_dict()}"
        )
    return cls


def _basic_candidates(type_name: str, profile: InstrumentProfile) -> list[dict[str, Any]]:
    base = {
        "type": type_name,
        "id": profile.instrument_id,
        "raw_symbol": profile.raw_symbol,
        "base_currency": profile.base_currency,
        "quote_currency": profile.quote_currency,
        "settlement_currency": profile.settlement_currency,
        "currency": profile.currency,
        "underlying": profile.underlying,
        "expiry": profile.expiry,
        "activation_ns": profile.activation_ns or 0,
        "expiration_ns": profile.expiration_ns or 0,
        "option_kind": profile.option_kind,
        "strike_price": profile.strike_price,
        "is_inverse": profile.is_inverse,
        "price_precision": profile.price_precision,
        "price_increment": profile.price_increment,
        "size_precision": profile.size_precision,
        "size_increment": profile.size_increment,
        "multiplier": profile.multiplier,
        "lot_size": profile.lot_size,
        "margin_init": profile.margin_init,
        "margin_maint": profile.margin_maint,
        "maker_fee": profile.maker_fee,
        "taker_fee": profile.taker_fee,
        "ts_event": profile.activation_ns or 0,
        "ts_init": profile.activation_ns or 0,
        "info": profile.metadata or {},
    }
    compact = {key: value for key, value in base.items() if value is not None}
    instrument_id_variant = dict(compact)
    instrument_id_variant["instrument_id"] = instrument_id_variant.pop("id")
    no_type_variant = dict(compact)
    no_type_variant.pop("type", None)
    return [compact, instrument_id_variant, no_type_variant]


def _adapter_error(cls, profile: InstrumentProfile, errors: list[dict]) -> NotImplementedError:
    support = describe_constructor_support(cls)
    lines = [
        "Unable to construct Nautilus instrument from InstrumentProfile.",
        f"instrument_type={profile.instrument_type!r}",
        f"selected_class={cls.__name__!r}",
        f"constructor_support={support}",
        f"profile={profile.to_dict()}",
        f"missing_fields={missing_fields_for_profile(profile)}",
        "from_dict_candidate_errors:",
    ]
    for index, error in enumerate(errors, start=1):
        lines.append(
            f"{index}. keys={error['keys']}, error={error['error']}, values={error['values']}"
        )
    return NotImplementedError("\n".join(lines))


def _signature_for(obj) -> str:
    if obj is None:
        return "<unavailable>"
    try:
        return str(inspect.signature(obj))
    except Exception as exc:
        return f"<signature unavailable: {exc}>"
