from dataclasses import dataclass
from decimal import Decimal

from nautilus_trader.backtest.engine import BacktestEngine
from nautilus_trader.config import BacktestEngineConfig
from nautilus_trader.config import LoggingConfig
from nautilus_trader.model.identifiers import TraderId


@dataclass(frozen=True)
class EngineRunConfig:
    venue: object
    oms_type: object
    account_type: object
    starting_balances: list
    base_currency: object | None = None
    default_leverage: Decimal = Decimal("1")
    trader_id: str = "BACKTEST_TRADER-001"
    log_level: str = "INFO"


class NautilusEngineRunner:
    def __init__(self, config: EngineRunConfig):
        self.config = config

    def create_engine(self):
        engine_config = BacktestEngineConfig(
            trader_id=TraderId(self.config.trader_id),
            logging=LoggingConfig(log_level=self.config.log_level),
        )
        return BacktestEngine(config=engine_config)

    def run(self, instrument, data, strategy):
        engine = self.create_engine()
        engine.add_venue(
            venue=self.config.venue,
            oms_type=self.config.oms_type,
            account_type=self.config.account_type,
            starting_balances=self.config.starting_balances,
            base_currency=self.config.base_currency,
            default_leverage=self.config.default_leverage,
        )
        engine.add_instrument(instrument)
        engine.add_data(data)
        engine.add_strategy(strategy)
        engine.run()
        return engine

    @staticmethod
    def dispose(engine) -> None:
        engine.dispose()
