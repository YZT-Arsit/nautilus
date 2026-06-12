"""合约/标的元数据接入。

合约信息也属于历史数据体系：可下载、归一化、按 ``exchange`` / ``as_of_date``
落盘复用。

公共接口::

    from data_engine.instruments import (
        InstrumentInfo,
        CcxtInstrumentProvider,
        StaticInstrumentProvider,
        instruments_to_polars,
        write_instruments_parquet,
    )

``ccxt`` / ``polars`` / ``pyarrow`` 全部懒加载，``import data_engine.instruments``
本身零重依赖、不触网。
"""
from data_engine.instruments.ccxt_provider import (
    CcxtInstrumentProvider,
    instrument_from_ccxt_market,
)
from data_engine.instruments.models import InstrumentInfo
from data_engine.instruments.registry import (
    INSTRUMENT_PARTITION_COLS,
    InstrumentProvider,
    StaticInstrumentProvider,
    instruments_to_polars,
    write_instruments_parquet,
)

__all__ = [
    "InstrumentInfo",
    "CcxtInstrumentProvider",
    "instrument_from_ccxt_market",
    "StaticInstrumentProvider",
    "InstrumentProvider",
    "instruments_to_polars",
    "write_instruments_parquet",
    "INSTRUMENT_PARTITION_COLS",
]
