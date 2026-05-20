from dataclasses import fields
from dataclasses import replace
from pathlib import Path

from nautilus_ext.instruments.instrument_profile import InstrumentProfile
from nautilus_ext.instruments.instrument_registry import InstrumentRegistry
from nautilus_ext.instruments.instrument_type_inferencer import InstrumentTypeInferencer
from nautilus_ext.instruments.nautilus_instrument_factory import NautilusInstrumentFactory


class AutoInstrumentProfileBuilder:
    @staticmethod
    def build_profile(
        symbol: str,
        data_root: str | Path | None = None,
        instrument_type: str | None = None,
        venue: str | None = None,
        hints: dict | None = None,
        require_explicit_type: bool = True,
        allow_inference: bool = False,
    ) -> InstrumentProfile:
        if not symbol:
            raise ValueError("symbol is required for instrument profile construction.")

        merged_hints = dict(hints or {})
        hinted_type = instrument_type or merged_hints.get("instrument_type")
        hinted_venue = venue or merged_hints.get("venue")

        if require_explicit_type and hinted_type is None:
            raise ValueError(
                "instrument_type must be provided explicitly. "
                "Automatic instrument type inference is disabled by default."
            )
        if hinted_venue is None:
            raise ValueError("venue must be provided explicitly.")

        normalized_symbol = AutoInstrumentProfileBuilder._normalize_symbol(symbol)
        inference = None
        if hinted_type is None and allow_inference:
            inference = InstrumentTypeInferencer.infer_from_path_and_symbol(
                path=data_root,
                symbol=normalized_symbol,
                hints=merged_hints,
            )
            hinted_type = inference.get("instrument_type")
            hinted_venue = hinted_venue or inference.get("venue")

        instrument_type_value = str(hinted_type).lower() if hinted_type is not None else "unknown"
        venue_value = str(hinted_venue).upper()

        registry = InstrumentRegistry()
        profile = None
        try:
            profile = registry.get(
                symbol=normalized_symbol,
                venue=venue_value,
                instrument_type=instrument_type_value,
            )
        except ValueError:
            profile = None

        metadata = {
            "data_root": str(data_root) if data_root is not None else None,
            "hints": merged_hints,
            "inference": inference,
        }
        if profile is not None:
            registry_metadata = dict(profile.metadata or {})
            registry_metadata.update(metadata)
            return AutoInstrumentProfileBuilder._apply_hints(
                replace(
                    profile,
                    source="manual+registry",
                    metadata=registry_metadata,
                ),
                merged_hints,
            )

        partial = InstrumentProfile(
            symbol=normalized_symbol,
            venue=venue_value,
            instrument_type=instrument_type_value,
            instrument_id=AutoInstrumentProfileBuilder._instrument_id(
                normalized_symbol,
                venue_value,
                instrument_type_value,
            ),
            raw_symbol=normalized_symbol,
            source="manual_partial",
            confidence=1.0 if not allow_inference else (inference or {}).get("confidence", 0.0),
            metadata=metadata,
        )
        return AutoInstrumentProfileBuilder._apply_hints(partial, merged_hints)

    @staticmethod
    def _apply_hints(profile: InstrumentProfile, hints: dict) -> InstrumentProfile:
        if not hints:
            return profile

        profile_fields = {field.name for field in fields(InstrumentProfile)}
        updates = {
            key: value
            for key, value in hints.items()
            if key in profile_fields and key not in {"symbol", "venue", "instrument_type"}
        }
        if not updates:
            return profile

        metadata = dict(profile.metadata or {})
        metadata["hint_overrides"] = updates
        return replace(profile, **updates, metadata=metadata)

    @staticmethod
    def _normalize_symbol(symbol: str) -> str:
        return str(symbol).upper()

    @staticmethod
    def _instrument_id(symbol: str, venue: str, instrument_type: str) -> str:
        if instrument_type == "crypto_perpetual":
            return f"{symbol}-PERP.{venue}"
        return f"{symbol}.{venue}"


class AutoInstrumentBuilder:
    @staticmethod
    def build(
        symbol: str,
        data_root: str | Path | None = None,
        instrument_type: str | None = None,
        venue: str | None = None,
        hints: dict | None = None,
        allow_test_fallback: bool = False,
        require_explicit_type: bool = True,
        allow_inference: bool = False,
    ):
        profile = AutoInstrumentProfileBuilder.build_profile(
            symbol=symbol,
            data_root=data_root,
            instrument_type=instrument_type,
            venue=venue,
            hints=hints,
            require_explicit_type=require_explicit_type,
            allow_inference=allow_inference,
        )
        try:
            return NautilusInstrumentFactory.build(profile)
        except NotImplementedError:
            if not allow_test_fallback:
                raise
            print(
                "WARNING: Using TestInstrumentProvider fallback; this is not a real instrument "
                "and must not be used for production backtests."
            )
            return AutoInstrumentBuilder._test_fallback(profile)

    @staticmethod
    def _test_fallback(profile: InstrumentProfile):
        try:
            from nautilus_trader.test_kit.providers import TestInstrumentProvider
        except Exception as exc:
            raise NotImplementedError(
                "TestInstrumentProvider fallback requested but unavailable in this environment. "
                f"Profile: {profile.to_dict()}, original_error={exc}"
            ) from exc

        if profile.instrument_type == "crypto_perpetual":
            return TestInstrumentProvider.btcusdt_binance()
        if profile.instrument_type == "currency_pair":
            return TestInstrumentProvider.default_fx_ccy("EUR/USD")
        return TestInstrumentProvider.eurusd_future(
            expiry_year=2024,
            expiry_month=3,
            venue_name=profile.venue if profile.venue != "UNKNOWN" else "XCME",
        )
