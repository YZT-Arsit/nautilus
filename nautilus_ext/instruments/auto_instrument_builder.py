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
    ) -> InstrumentProfile:
        if not symbol:
            raise ValueError("symbol is required for automatic instrument profile inference.")

        merged_hints = dict(hints or {})
        if instrument_type is not None:
            merged_hints["instrument_type"] = instrument_type
        if venue is not None:
            merged_hints["venue"] = venue

        normalized_symbol = AutoInstrumentProfileBuilder._normalize_symbol(symbol)
        inference = InstrumentTypeInferencer.infer_from_path_and_symbol(
            path=data_root,
            symbol=normalized_symbol,
            hints=merged_hints,
        )

        registry = InstrumentRegistry()

        try:
            profile = registry.get(
                symbol=normalized_symbol,
                venue=inference.get("venue"),
                instrument_type=inference.get("instrument_type"),
            )
        except ValueError:
            if str(inference.get("venue") or "").upper() == "UNKNOWN":
                try:
                    profile = registry.get(
                        symbol=normalized_symbol,
                        instrument_type=inference.get("instrument_type"),
                    )
                except ValueError:
                    profile = None
            else:
                profile = None

        if profile is not None:
            metadata = dict(profile.metadata or {})
            metadata["inference"] = inference
            return replace(
                profile,
                source=f"{profile.source}+inference",
                confidence=max(profile.confidence, inference.get("confidence", 0.0)),
                metadata=metadata,
            )

        inferred_type = inference.get("instrument_type", "unknown")
        inferred_venue = str(inference.get("venue") or "UNKNOWN").upper()
        if inferred_type == "unknown":
            return InstrumentProfile(
                symbol=normalized_symbol,
                venue=inferred_venue,
                instrument_type="unknown",
                instrument_id=f"{normalized_symbol}.{inferred_venue}",
                raw_symbol=normalized_symbol,
                source="path",
                confidence=0.0,
                metadata={"inference": inference},
            )

        return InstrumentProfile(
            symbol=normalized_symbol,
            venue=inferred_venue,
            instrument_type=inferred_type,
            instrument_id=AutoInstrumentProfileBuilder._instrument_id(
                normalized_symbol,
                inferred_venue,
                inferred_type,
            ),
            raw_symbol=normalized_symbol,
            base_currency=merged_hints.get("base_currency"),
            quote_currency=merged_hints.get("quote_currency"),
            settlement_currency=merged_hints.get("settlement_currency"),
            currency=merged_hints.get("currency"),
            asset_class=inference.get("asset_class"),
            exchange=merged_hints.get("exchange"),
            exchange_symbol=merged_hints.get("exchange_symbol"),
            price_precision=merged_hints.get("price_precision"),
            size_precision=merged_hints.get("size_precision"),
            price_increment=merged_hints.get("price_increment"),
            size_increment=merged_hints.get("size_increment"),
            maker_fee=merged_hints.get("maker_fee"),
            taker_fee=merged_hints.get("taker_fee"),
            margin_init=merged_hints.get("margin_init"),
            margin_maint=merged_hints.get("margin_maint"),
            multiplier=merged_hints.get("multiplier"),
            lot_size=merged_hints.get("lot_size"),
            expiry=merged_hints.get("expiry"),
            activation_ns=merged_hints.get("activation_ns"),
            expiration_ns=merged_hints.get("expiration_ns"),
            option_kind=merged_hints.get("option_kind"),
            strike_price=merged_hints.get("strike_price"),
            underlying=merged_hints.get("underlying"),
            settlement_type=merged_hints.get("settlement_type"),
            is_inverse=merged_hints.get("is_inverse"),
            synthetic_formula=merged_hints.get("synthetic_formula"),
            components=merged_hints.get("components"),
            source="inferred_partial",
            confidence=inference.get("confidence", 0.0),
            metadata={"inference": inference, "hints": merged_hints},
        )

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
    ):
        profile = AutoInstrumentProfileBuilder.build_profile(
            symbol=symbol,
            data_root=data_root,
            instrument_type=instrument_type,
            venue=venue,
            hints=hints,
        )
        try:
            return NautilusInstrumentFactory.build(profile)
        except NotImplementedError:
            if not allow_test_fallback:
                raise
            print(
                "WARNING: Using TestInstrumentProvider fallback; this is not a real instrument "
                "and should not be used for production backtests."
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
