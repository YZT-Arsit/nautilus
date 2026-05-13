from nautilus_trader.model.data import BarType


class BarTypeFactory:
    @staticmethod
    def create(
        instrument,
        timeframe: str,
        price_type: str = "LAST",
        source: str = "EXTERNAL",
    ):
        return BarType.from_str(f"{instrument.id}-{timeframe}-{price_type}-{source}")
