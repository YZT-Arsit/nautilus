"""data_engine.transforms — 纯 Python 的行情变换（不依赖 polars / pyarrow / Nautilus）。

* :mod:`tick_to_bar` —— tick / quote-tick / bar 聚合成标准 OHLCV 分钟线。
* :mod:`bars` —— bar 级工具：粗化重采样、合法性校验、交易日推导。

这些函数只用标准库，因此在任何普通 Python 环境都能运行、测试、复用。
落盘（Hive Parquet）由 ``feature_engine.services`` / ``storage`` 负责，本层
只产出标准事件（:class:`data_engine.events.BarEvent`）与标准 OHLCV 行（dict）。
"""
from data_engine.transforms.bars import (
    derive_trading_date,
    parse_frequency,
    resample_bars,
    validate_bars,
)
from data_engine.transforms.tick_to_bar import (
    MinuteBarResult,
    aggregate_ticks_to_bars,
)

__all__ = [
    "aggregate_ticks_to_bars",
    "MinuteBarResult",
    "parse_frequency",
    "resample_bars",
    "validate_bars",
    "derive_trading_date",
]
