# 历史数据体系目录结构

> 本文件定义统一历史数据体系的目录结构与分区约定。完整的数据接入说明、
> 使用示例与未完成项见 [数据接入与历史数据体系说明.md](数据接入与历史数据体系说明.md)。

## 1. 总体结构

`historical_data` 是统一历史数据体系，包含三类数据 + manifest：

```
historical_data/
    market_data/        # 历史行情数据（bar / tick / OHLCV）
    feature_data/       # 历史特征数据（MA / RSI / VWM / volatility / derived）
    instruments/        # 合约/标的信息（symbol / precision / tick / expiry ...）
    manifests/          # 特征版本与参数登记
```

特征数据生成后不是临时变量，而是历史数据的一部分：落盘、登记 manifest、
供训练 / 回测 / 实盘 warmup 复用。market_data、feature_data、instruments
都以 Hive-style Parquet 管理。

## 2. market_data（历史行情数据）

分区列：`(asset_class, exchange, frequency, trading_date)`

```
market_data/
    asset_class=<asset_class>/
        exchange=<exchange>/
            frequency=<frequency>/
                trading_date=<YYYY-MM-DD>/
                    part-*.parquet
```

实现：`feature_engine.storage.ParquetStore`（写）/
`data_engine.sources.parquet_bars`（读为 BarEvent）。

## 3. feature_data（历史特征数据）

分区列：`(feature_group, frequency, trading_date)`

```
feature_data/
    feature_group=<feature_group>/      # 如 technical / volume
        frequency=<frequency>/
            trading_date=<YYYY-MM-DD>/
                part-*.parquet
```

实现：`feature_engine.streaming.EodArchiver`（写）/
`feature_engine.storage.FeatureDataReader`（读 / 查询）。

> **instrument 维度**：当前实现中 `instrument_id` **不在分区路径里**，而是在
> 数据体的 `symbol` 列里。`FeatureDataReader.scan_features(instrument_id=...)`
> 通过对 `symbol` 列等值过滤来支持按标的查询。后续数据规模变大时，可把
> `instrument_id` 升级为分区列。

## 4. instruments（合约/标的信息）

分区列：`(exchange, as_of_date)`

```
instruments/
    exchange=<exchange>/
        as_of_date=<YYYY-MM-DD>/
            part-*.parquet
```

实现：`data_engine.instruments.write_instruments_parquet`。

## 5. manifests

```
manifests/
    manifest-*.parquet
```

每行记录：`partition_key, feature_name, version, params_hash, computed_at,
row_count, source`。实现：`feature_engine.storage.Manifest`。

## 6. 与代码的一致性

| 概念 | 代码位置 |
| --- | --- |
| 分区路径构造/解析 | `feature_engine/storage/layout.py` |
| Hive Parquet 读写 | `feature_engine/storage/parquet_store.py` |
| manifest | `feature_engine/storage/metadata.py` |
| 收盘归档（raw + feature 落盘 + manifest） | `feature_engine/streaming/archiver.py` |
| 历史特征查询 | `feature_engine/storage/feature_reader.py` |
| 合约信息落盘 | `data_engine/instruments/registry.py` |
