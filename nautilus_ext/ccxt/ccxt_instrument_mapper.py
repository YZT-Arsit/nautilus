"""
Map a ccxt market dict to a Nautilus InstrumentProfile, then build the
Nautilus Instrument via the existing NautilusInstrumentFactory pipeline.

Supported instrument types
--------------------------
    spot   → CurrencyPair
    swap   → CryptoPerpetual   (linear or inverse)
    future → CryptoFuture      (linear or inverse, with expiry)

Fields that ccxt may not provide (and the mapper cannot safely infer) are
left as None in the profile, which causes NautilusInstrumentFactory to
raise a clear error listing exactly which fields are missing.  The user
should then pass an InstrumentProfile override.
"""
from __future__ import annotations

import logging
from decimal import Decimal

from nautilus_ext.ccxt.ccxt_config import CcxtDataConfig
from nautilus_ext.instruments.instrument_profile import InstrumentProfile
from nautilus_ext.instruments.nautilus_instrument_factory import NautilusInstrumentFactory

log = logging.getLogger(__name__)

# ccxt precisionMode constants (matches ccxt source)
_DECIMAL_PLACES = 2
_TICK_SIZE = 4
_SIGNIFICANT_DIGITS = 3


class CcxtInstrumentMapper:
    """Convert a ccxt market metadata dict into a Nautilus Instrument."""

    def __init__(self, config: CcxtDataConfig, precision_mode: int = _DECIMAL_PLACES) -> None:
        self.config = config
        self.precision_mode = precision_mode

    # ------------------------------------------------------------------
    # Public
    # ------------------------------------------------------------------

    def build_instrument(self, market: dict, market_type: str):
        """Build and return a Nautilus Instrument from a ccxt market dict.

        Parameters
        ----------
        market : dict
            Raw ccxt market metadata as returned by exchange.load_markets().
        market_type : str
            One of "spot", "swap_linear", "swap_inverse",
            "future_linear", "future_inverse".

        Raises
        ------
        ValueError
            If required fields cannot be extracted from the market dict.
        NotImplementedError
            If the instrument type is not yet supported.
        """
        profile = self._to_profile(market, market_type)
        return NautilusInstrumentFactory.build(profile)

    def to_profile(self, market: dict, market_type: str) -> InstrumentProfile:
        """Return the intermediate InstrumentProfile (useful for debugging)."""
        return self._to_profile(market, market_type)

    # ------------------------------------------------------------------
    # Core mapping logic
    # ------------------------------------------------------------------

    def _to_profile(self, market: dict, market_type: str) -> InstrumentProfile:
        venue = self.config.resolved_venue

        base = (self.config.base_currency or market.get("base", "")).upper()
        quote = (self.config.quote_currency or market.get("quote", "")).upper()
        settle = (market.get("settle") or quote or "").upper()

        if not base or not quote:
            raise ValueError(
                f"Cannot extract base/quote currency from market for symbol "
                f"{market.get('symbol')!r}.  Market keys: {list(market.keys())}"
            )

        raw_symbol = market.get("symbol", f"{base}/{quote}")
        normalized_symbol = f"{base}{quote}"  # e.g. "BTCUSDT"

        price_precision, price_increment = self._parse_precision(
            market.get("precision", {}).get("price"), self.precision_mode
        )
        size_precision, size_increment = self._parse_precision(
            market.get("precision", {}).get("amount"), self.precision_mode
        )

        # Fall back to limits if precision fields are missing / zero
        if price_increment == "1" and price_precision == 0:
            limit_min_price = market.get("limits", {}).get("price", {}).get("min")
            if limit_min_price and float(limit_min_price) < 1:
                price_precision, price_increment = self._parse_precision(
                    limit_min_price, _TICK_SIZE
                )
        if size_increment == "1" and size_precision == 0:
            limit_min_amount = market.get("limits", {}).get("amount", {}).get("min")
            if limit_min_amount and float(limit_min_amount) < 1:
                size_precision, size_increment = self._parse_precision(
                    limit_min_amount, _TICK_SIZE
                )

        taker = str(market.get("taker") or "0")
        maker = str(market.get("maker") or "0")
        contract_size = market.get("contractSize")
        multiplier = str(contract_size) if contract_size and float(contract_size) > 0 else "1"
        is_inverse = bool(market.get("inverse", False))
        expiry_dt = market.get("expiryDatetime") or market.get("expiry")
        expiry_str = str(expiry_dt) if expiry_dt else None

        if market_type == "spot":
            instrument_type = "currency_pair"
            instrument_id = f"{normalized_symbol}.{venue}"
            settlement_currency = None
        elif market_type in ("swap_linear", "swap_inverse"):
            instrument_type = "crypto_perpetual"
            instrument_id = f"{normalized_symbol}-PERP.{venue}"
            settlement_currency = settle
        elif market_type in ("future_linear", "future_inverse"):
            instrument_type = "crypto_future"
            expiry_label = self._expiry_label(expiry_str, market)
            instrument_id = f"{normalized_symbol}-{expiry_label}.{venue}" if expiry_label else f"{normalized_symbol}-FUT.{venue}"
            settlement_currency = settle
        else:
            raise NotImplementedError(
                f"market_type={market_type!r} is not supported for instrument construction. "
                f"Supported: spot, swap_linear, swap_inverse, future_linear, future_inverse."
            )

        profile = InstrumentProfile(
            symbol=normalized_symbol,
            venue=venue,
            instrument_type=instrument_type,
            instrument_id=instrument_id,
            raw_symbol=raw_symbol,
            base_currency=base or None,
            quote_currency=quote or None,
            settlement_currency=settlement_currency or None,
            is_inverse=is_inverse,
            price_precision=price_precision,
            price_increment=price_increment,
            size_precision=size_precision,
            size_increment=size_increment,
            multiplier=multiplier,
            lot_size=size_increment,
            maker_fee=maker,
            taker_fee=taker,
            margin_init="0",
            margin_maint="0",
            expiry=expiry_str,
            source="ccxt",
            confidence=0.9,
            metadata={
                "ccxt_market_id": market.get("id"),
                "ccxt_market_type": market_type,
                "ccxt_symbol": raw_symbol,
                "ccxt_exchange": self.config.exchange_id,
            },
        )
        return profile

    # ------------------------------------------------------------------
    # Precision helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_precision(value, precision_mode: int) -> tuple[int, str]:
        """Return (decimal_places: int, increment_str: str).

        ccxt has two main precision modes:
        - DECIMAL_PLACES (2): value is an integer counting decimal places.
        - TICK_SIZE (4): value is a float equal to the tick size.
        """
        if value is None:
            return 8, "0.00000001"

        if precision_mode == _TICK_SIZE:
            return CcxtInstrumentMapper._from_tick_size(float(value))
        else:
            return CcxtInstrumentMapper._from_decimal_places(int(value))

    @staticmethod
    def _from_tick_size(tick: float) -> tuple[int, str]:
        if tick <= 0:
            return 8, "0.00000001"
        d = Decimal(str(tick))
        sign, digits, exponent = d.as_tuple()
        decimal_places = max(0, -exponent)
        increment_str = format(d, "f")
        return decimal_places, increment_str

    @staticmethod
    def _from_decimal_places(dp: int) -> tuple[int, str]:
        dp = max(0, dp)
        if dp == 0:
            return 0, "1"
        increment_str = "0." + "0" * (dp - 1) + "1"
        return dp, increment_str

    # ------------------------------------------------------------------
    # Expiry helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _expiry_label(expiry_str: str | None, market: dict) -> str:
        """Return a short label like '20230930' for use in instrument IDs."""
        if not expiry_str:
            return ""
        try:
            import pandas as pd
            ts = pd.Timestamp(expiry_str)
            return ts.strftime("%Y%m%d")
        except Exception:
            # ccxt sometimes stores epoch seconds
            try:
                expiry_int = market.get("expiry")
                if expiry_int:
                    import pandas as pd
                    # If it's milliseconds
                    unit = "ms" if int(expiry_int) > 1e12 else "s"
                    ts = pd.Timestamp(int(expiry_int), unit=unit)
                    return ts.strftime("%Y%m%d")
            except Exception:
                pass
            return ""
