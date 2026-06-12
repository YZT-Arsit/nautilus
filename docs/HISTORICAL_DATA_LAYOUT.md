# 历史数据目录结构（HISTORICAL_DATA_LAYOUT）

历史数据体系由三类**平级数据资产** + manifests 组成，统一 Hive-style Parquet：

```
historical_data/
    market_data/      # 历史行情
        asset_class=<asset_class>/
        exchange=<exchange>/
        frequency=<frequency>/
        trading_date=<YYYY-MM-DD>/
        instrument_id=<instrument_id>/
            part-*.parquet

    feature_data/     # 历史特征（与 market_data 平级，不是临时变量）
        feature_group=<feature_group>/
        asset_class=<asset_class>/
        exchange=<exchange>/
        frequency=<frequency>/
        trading_date=<YYYY-MM-DD>/
        instrument_id=<instrument_id>/
            part-*.parquet

    instruments/      # 合约/标的信息
        exchange=<exchange>/
        as_of_date=<YYYY-MM-DD>/
            part-*.parquet

    manifests/
        dataset_manifest/   # 行情批次
        feature_manifest/   # 特征批次/版本/参数/依赖/row_count/source/computed_at
```

**核心理念**：特征数据一旦计算落盘，就是历史数据资产，可供训练、回测、实盘
warmup 复用——和行情数据平级，按相同维度（asset_class/exchange/frequency/
trading_date/instrument_id）分区，多标的天然可复用。

## 路径构造（唯一真源）

路径构造集中在 `feature_engine/storage/layout.py`（**纯标准库**），writer/reader
共享，保证读写分区一致：

```python
from feature_engine.storage.layout import (
    market_data_path, feature_data_path, instruments_path,
    MARKET_DATA_PARTITION_COLS, FEATURE_DATA_PARTITION_COLS, INSTRUMENTS_PARTITION_COLS,
)

market_data_path("historical_data/market_data",
    asset_class="future", exchange="CFFEX", frequency="1m",
    trading_date="2026-05-26", instrument_id="IH2303.CFFEX")
# -> historical_data/market_data/asset_class=future/exchange=CFFEX/
#    frequency=1m/trading_date=2026-05-26/instrument_id=IH2303.CFFEX
```

分区列顺序：

| 资产 | 分区列 |
|------|--------|
| market_data | `asset_class, exchange, frequency, trading_date, instrument_id` |
| feature_data（新版） | `feature_group, asset_class, exchange, frequency, trading_date, instrument_id` |
| instruments | `exchange, as_of_date` |

## market_data 标准 OHLCV schema

由 `data_engine.transforms` 产出、`services.MinuteBarBuilder` 落盘：

```
instrument_id, symbol, ts_event, open, high, low, close, volume,
turnover, trading_date, frequency, volume_is_synthetic
```

- `symbol == instrument_id`（feature_engine 期望的列名）。
- **`volume_is_synthetic`**：当成交量来自“tick 计数”（输入无真实 size）时为
  `True`，绝不冒充真实成交量。
- OHLC 合法性（`low <= open/close <= high`）、重复时间戳、时间单调性在写盘前由
  `transforms.validate_bars` 校验，`MinuteBarBuilder.write_market_data(strict=True)`
  默认校验不过即拒写。

## legacy 兼容

旧版 feature_data 分区为 `(feature_group, frequency, trading_date)`，
`instrument_id` 不在路径里、而作为数据体 `symbol` 列存在
（`LEGACY_FEATURE_PARTITION_COLS`）。

兼容策略：
- **旧数据仍可读**：`FeatureDataReader` 查询接口不变，按 `symbol` 列做等值过滤。
- **新写入默认用新分区**：`services.HistoricalFeatureBuilder.write_feature_data`
  使用 `FEATURE_DATA_PARTITION_COLS`（instrument 提升为分区维度）。
- 现有 `scripts/build_historical_features.py` 仍走 `EodArchiver`（legacy 分区），
  迁移到新分区是 backlog（见 README/最终报告）。

## Backlog（尚未实现，明确标注）

- **交易日归属**：`derive_trading_date` 目前用 UTC 日历日；期货**夜盘**计入次一
  交易日的规则未实现。需要精确夜盘归属时，请在 build 时显式传 `trading_date`。
- **新旧 feature_data 分区统一**：把 `build_historical_features.py` 从 EodArchiver
  迁到 `HistoricalFeatureBuilder.write_feature_data`，并提供旧→新一次性迁移工具。
- **dataset_manifest（行情批次）**：当前 `MinuteBarBuilder` 写 market_data 但尚未
  写 dataset_manifest；feature_manifest 已由 `HistoricalFeatureBuilder` 支持。
