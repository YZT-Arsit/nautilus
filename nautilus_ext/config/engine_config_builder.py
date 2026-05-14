from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class EngineConfigProfile:
    venue: str
    account_type: str = "MARGIN"
    oms_type: str = "NETTING"
    starting_balance: float = 1_000_000
    account_currency: str | None = None
    default_leverage: Decimal = Decimal("1")
    log_level: str = "INFO"


class AutoEngineConfigBuilder:
    @staticmethod
    def build(
        venue: str,
        instrument_profile=None,
        starting_balance: float = 1_000_000,
        account_currency: str | None = None,
        account_type: str = "MARGIN",
        oms_type: str = "NETTING",
        default_leverage: Decimal = Decimal("1"),
        log_level: str = "INFO",
    ):
        currency_code = AutoEngineConfigBuilder._infer_account_currency(
            explicit_currency=account_currency,
            instrument_profile=instrument_profile,
        )
        currency = AutoEngineConfigBuilder._resolve_currency(currency_code)

        from nautilus_ext.runners import EngineRunConfig
        from nautilus_trader.model.enums import AccountType
        from nautilus_trader.model.enums import OmsType
        from nautilus_trader.model.identifiers import Venue
        from nautilus_trader.model.objects import Money

        return EngineRunConfig(
            venue=Venue(venue),
            oms_type=AutoEngineConfigBuilder._enum_value(OmsType, oms_type, "oms_type"),
            account_type=AutoEngineConfigBuilder._enum_value(
                AccountType,
                account_type,
                "account_type",
            ),
            starting_balances=[Money(starting_balance, currency)],
            base_currency=currency,
            default_leverage=default_leverage,
            log_level=log_level,
        )

    @staticmethod
    def _infer_account_currency(explicit_currency: str | None, instrument_profile) -> str:
        if explicit_currency:
            return explicit_currency.upper()
        if instrument_profile is not None:
            settlement_currency = getattr(instrument_profile, "settlement_currency", None)
            if settlement_currency:
                return str(settlement_currency).upper()
            quote_currency = getattr(instrument_profile, "quote_currency", None)
            if quote_currency:
                return str(quote_currency).upper()

        print(
            "WARNING: Unable to infer account currency from instrument profile; "
            "falling back to USD."
        )
        return "USD"

    @staticmethod
    def _resolve_currency(currency_code: str):
        code = currency_code.upper()
        try:
            import nautilus_trader.model.currencies as currencies

            currency = getattr(currencies, code, None)
            if currency is not None:
                return currency
        except Exception:
            pass

        try:
            from nautilus_trader.model.objects import Currency

            return Currency.from_str(code)
        except Exception as exc:
            raise ValueError(
                f"Unable to resolve account currency {currency_code!r}. "
                "Supported common currencies include USD, USDT, USDC, EUR, GBP, "
                "JPY, BTC, and ETH, or any currency accepted by Nautilus Currency.from_str()."
            ) from exc

    @staticmethod
    def _enum_value(enum_cls, value: str, field_name: str):
        normalized = value.upper()
        enum_value = getattr(enum_cls, normalized, None)
        if enum_value is None:
            raise ValueError(
                f"Unsupported {field_name}={value!r}. "
                f"Available values: {[item.name for item in enum_cls]}"
            )
        return enum_value
