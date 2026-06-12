# 模块边界（MODULE_BOUNDARIES）

本项目从 nautilus_trader fork 扩展，但**自研的数据接入 / 特征处理是独立、可复用
框架**，不与 Nautilus 耦合。Nautilus 只能作为**可选**数据源 adapter 或下游执行
后端。本文件规定各层能 import 什么、不能 import 什么，并给出可执行的边界测试。

## 分层与依赖方向

```
data_engine        （数据接入：BarEvent / CSV / Parquet / 分钟线 / 合约信息）
    -> feature_engine   （特征处理：FeatureSpec/DAG/Batch/Streaming/Storage）
    -> strategies        （信号逻辑：只产出 BUY/SELL/HOLD）
    -> strategy_framework（编排：registry / output / execution / backends）
```

依赖只能**自上而下**。下层不得 import 上层；执行/策略层不得反向污染数据接入层。

## data_engine —— 自研数据接入层

**能 import**：标准库；`data_engine` 自身。

**不能 import（顶层）**：
- ❌ `nautilus_trader`（仅 `data_engine/adapters/nautilus_catalog.py` 这一个
  *adapter* 文件允许，且必须**懒加载**、不被任何 `__init__` 预导入）。
- ❌ `feature_engine`（数据层不依赖特征层）。
- ❌ `strategy_framework` / `strategies`。
- ❌ `polars` / `pyarrow` / `pandas` 的**顶层** import —— 必须懒加载，保证
  `import data_engine` 在纯 Python 环境零重依赖。

**职责**：`BarEvent` 等中性事件、synthetic/csv_bars/parquet_bars/
hive_parquet_bars/live_gateway 数据源、`transforms`（tick→分钟线、重采样、
校验）、`instruments`（合约信息 + 可选 Parquet 落盘）、`adapters`
（DataFrame 桥接、可选 Nautilus catalog）。

CTP / 真实柜台 / Nautilus catalog / QuoteTick 接入**只能**放在
`data_engine/sources/`（provider 占位）或 `data_engine/adapters/`，**不进 core**。

## feature_engine —— 自研特征处理层

**能 import**：标准库；`polars` / `pyarrow`（特征层允许的重依赖）；`data_engine`
（向下依赖，用于 DataFrame 桥接）；`feature_engine` 自身。

**不能 import**：
- ❌ `nautilus_trader` 的执行 / 回测 / 撮合模块（`nautilus_trader.backtest` /
  `.execution` / `.trading`）。`core` / `execution` / `storage` 一律不得依赖
  Nautilus 执行内部。
- ❌ `strategy_framework` / `strategies`。

> 注：`feature_engine/nautilus_indicators.py` 是历史遗留的可选指标适配，仅触达
> Nautilus *indicators*（非执行/回测），且懒加载；新代码请勿依赖它。

**职责**：`core`（Feature/FeatureDAG/registry/state/schema）、`features`
（sma_20/rsi_14/macd/vol_30/vwm_20/vwm_zscore_60 ...）、`execution`
（BatchEngine）、`streaming`（StreamingEngine/EodArchiver）、`storage`
（ParquetStore/Manifest/FeatureDataReader/MarketDataReader/layout）、`services`
（HistoricalFeatureBuilder / MinuteBarBuilder）。

离线 batch 与流式 micro-batch 共享同一 `Feature.update(batch)` 语义（由
`test_streaming_batch_parity` 保证一致）。

## adapters / providers 的职责

- `data_engine/adapters/nautilus_catalog.py`：**唯一**允许 import Nautilus 的
  data_engine 文件。把 Nautilus `QuoteTick` 归一化成中性 tick，再交给
  `transforms` 聚合。Nautilus 缺失时**导入仍成功**（懒加载），调用时给清晰错误。
- `data_engine/sources/live_gateway.py` + `sources/providers/<name>.py`：实盘
  网关骨架。真实 CTP/柜台 connector 是占位，未实现时抛清晰 `NotImplementedError`
  / `ImportError`。**账号密码只走环境变量名，绝不写进配置 / 对象属性**。
- `data_engine/instruments/{ccxt_provider,ctp_provider}.py`：合约信息 provider。
  ccxt 懒加载；ctp 为占位（未实现即清晰报错）。

## scripts 的职责

`scripts/` 只负责 `argparse` + 调用 `feature_engine.services` / `data_engine`，
**业务逻辑不写在脚本里**。可复用逻辑沉淀到 service：
- `scripts/build_minute_bars.py` → `services.MinuteBarBuilder`
- `scripts/build_historical_features.py` → 特征计算（现用 StreamingEngine，
  迁移到 `services.HistoricalFeatureBuilder` 见 backlog）
- `scripts/validate_data_engine.py` / `validate_feature_engine.py` —— 最小验证。

## 可执行的边界测试

- `data_engine/tests/test_decoupling.py`
  - `import data_engine` 仅用标准库即可成功；
  - 扫描 data_engine core 所有 `.py`，禁止**顶层** import
    `nautilus_trader` / `polars` / `pyarrow` / `pandas`
    （`nautilus_catalog.py` 例外，且也懒加载）；
  - `bars_to_polars` 在无 polars 时给出清晰 `ImportError`。

> 约定：凡是“重依赖”（polars/pyarrow/ccxt/nautilus），data_engine 一律**懒加载**
> （函数内部 import），保证 `import data_engine` 在任何环境都零重依赖。
