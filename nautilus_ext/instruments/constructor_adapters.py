from __future__ import annotations

from typing import Any

from nautilus_ext.instruments.instrument_profile import InstrumentProfile


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
