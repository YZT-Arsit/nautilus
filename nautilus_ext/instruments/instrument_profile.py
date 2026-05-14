from dataclasses import asdict
from dataclasses import dataclass
from typing import Any


SUPPORTED_INSTRUMENT_TYPES = {
    "equity",
    "currency_pair",
    "commodity",
    "index",
    "futures_contract",
    "futures_spread",
    "crypto_future",
    "crypto_perpetual",
    "perpetual_contract",
    "option_contract",
    "option_spread",
    "crypto_option",
    "binary_option",
    "cfd",
    "betting",
    "synthetic",
    "unknown",
}


@dataclass(frozen=True)
class InstrumentProfile:
    symbol: str
    venue: str
    instrument_type: str
    instrument_id: str
    raw_symbol: str

    base_currency: str | None = None
    quote_currency: str | None = None
    settlement_currency: str | None = None

    asset_class: str | None = None
    exchange: str | None = None
    exchange_symbol: str | None = None

    price_precision: int | None = None
    size_precision: int | None = None
    price_increment: str | None = None
    size_increment: str | None = None

    maker_fee: str | None = None
    taker_fee: str | None = None

    margin_init: str | None = None
    margin_maint: str | None = None
    multiplier: str | None = None

    expiry: str | None = None
    activation_ns: int | None = None
    expiration_ns: int | None = None

    option_kind: str | None = None
    strike_price: str | None = None

    underlying: str | None = None
    settlement_type: str | None = None
    is_inverse: bool | None = None

    source: str = "unknown"
    confidence: float = 0.0
    metadata: dict[str, Any] | None = None

    def __post_init__(self):
        if self.instrument_type not in SUPPORTED_INSTRUMENT_TYPES:
            raise ValueError(
                f"Unsupported instrument_type={self.instrument_type!r}. "
                f"Supported types: {sorted(SUPPORTED_INSTRUMENT_TYPES)}"
            )

    def to_dict(self) -> dict:
        return asdict(self)
