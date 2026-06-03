# Feature Data Layer 设计文档

**项目**: nautilus_ext（NautilusTrader 量化交易扩展）  
**状态**: 已实现  
**路径**: `nautilus_ext/features/`，`nautilus_ext/ml/`

---

## 目录

1. [为什么特征要和历史数据平级](#1-为什么特征要和历史数据平级)
2. [当前 SignalResult.debug / signals.csv 为什么不够](#2-当前-signalresultdebug--signalscsv-为什么不够)
3. [核心组件职责](#3-核心组件职责)
4. [Online 低延迟路径与 Offline 持久化路径](#4-online-低延迟路径与-offline-持久化路径)
5. [历史特征、warmup 特征、live 特征的统一](#5-历史特征warmup-特征live-特征的统一)
6. [特征如何复用到多个策略](#6-特征如何复用到多个策略)
7. [特征如何流向未来训练与推理](#7-特征如何流向未来训练与推理)
8. [如何减少数据类型转换](#8-如何减少数据类型转换)
9. [如何加新特征（步骤）](#9-如何加新特征步骤)
10. [如何加新策略（步骤）](#10-如何加新策略步骤)
11. [DataHander 参考审计](#11-datahander-参考审计)
12. [当前实现边界与未来 TradingNode 接入](#12-当前实现边界与未来-tradingnode-接入)

---

## 1. 为什么特征要和历史数据平级

### 特征是一等公民（first-class data asset）

在量化系统中，特征（Feature）不是信号引擎的内部"调试字段"，而是独立的、版本化的、可审计的数据资产，其地位应当与 OHLCV 行情数据平级。具体理由如下：

**可再现性（Reproducibility）**：若特征只存在于策略的运行时内存中，则无法独立于策略逻辑重新审计一次历史信号决策。一旦策略代码发生任何改动，历史特征值就无从比对。

**去耦合（Decoupling）**：将特征计算从信号逻辑中分离出来，使得多个策略可以订阅同一组特征，而不需要每个策略各自维护一套重复的指标计算逻辑。

**训练数据（Training Data）**：机器学习模型的训练需要与生产环境完全一致的 point-in-time 特征历史。若特征不被显式存储，则训练时只能重新计算，这会引入代码差异导致的特征不一致，乃至 look-ahead bias。

**版本化与 schema 管理**：特征字段的增删或语义变化应当触发版本号变更，而非静默地破坏下游训练流水线。这与市场数据格式变更需要 schema migration 同理。

**点位时间正确性（Point-in-Time Correctness）**：warmup 阶段计算的特征（`is_warmup=True`）使用了回测起点前的历史数据，若混入训练集，则相当于向模型泄露了未来信息。特征系统必须将这类特征明确标记并在训练时过滤。

### 与 nautilus_ext 现有数据层的类比

| 数据层 | 存储形式 | 标识键 | 版本化 |
|--------|---------|--------|--------|
| Raw 行情 | `raw/*.parquet` | instrument_id + ts | 无 |
| Normalized bars | `normalized/*.parquet` | instrument_id + ts + timeframe | 无 |
| **Feature Data** | `features/offline/{feature_set_id}/{instrument_id}/*.parquet` | instrument_id + feature_set_id + ts | `feature_version` + JSON schema 文件 |

---

## 2. 当前 SignalResult.debug / signals.csv 为什么不够

### `SignalResult.debug` 的结构问题

当前 `SignalResult`（定义于 `nautilus_ext/strategies/interfaces/output_types.py`）包含一个 `debug: dict | None` 字段：

```python
@dataclass(frozen=True)
class SignalResult:
    signal_name: str | None = None
    order_intents: list[OrderIntent] = field(default_factory=list)
    debug: dict | None = None   # 问题所在
    state: dict | None = None
    reason: str | None = None
    ...
```

`debug` 字段的缺陷：

| 缺陷 | 说明 |
|------|------|
| **无 schema** | 任何键值对都可以写入，字段无类型约束，无描述，无版本 |
| **无版本控制** | 新增/删除特征字段不触发任何版本检查 |
| **生命周期绑定信号** | `debug` 随 `SignalResult` 一同消亡，不能被独立持久化或查询 |
| **无法跨策略共享** | 每个策略实例独立计算自己的 debug 字段 |
| **写入 signals.csv 后无法反查** | `signals.csv` 是信号决策记录，不是特征数据库 |
| **look-ahead 风险** | warmup 阶段的 debug 字段被写入 signals.csv，训练时若不过滤则引入未来信息 |

### `signals.csv` 的局限

`signals.csv` 是运行时 `SignalRecorder` 写出的信号流水记录，其列结构随着 `debug` 的内容自由变化，没有固定 schema。它的职责是**记录信号决策**，而不是**存储特征数据**。两者混用会导致：

- CSV 列数随特征增减而变化，历史文件无法与新格式兼容
- 训练脚本需要了解 `signals.csv` 的内部列名，与信号引擎实现深度耦合
- 无法在 `signals.csv` 粒度上做 point-in-time 过滤（warmup / live 混在一起）

### Feature Data Layer 如何解决这些问题

```
SignalResult.debug["vwm"] = 0.042        # 旧方式：无 schema，无版本
↓
FeatureEvent(                            # 新方式：typed, versioned, separable
    feature_set_id="vwm_features_v1",
    feature_version="1",
    values={"vwm": 0.042, "atr": 0.0015, ...},
    is_warmup=False,
)
```

---

## 3. 核心组件职责

### 文件总览

| 文件 | 所在包 | 职责 |
|------|--------|------|
| `feature_event.py` | `features/` | `FeatureEvent` frozen dataclass，online/offline 序列化 |
| `feature_schema.py` | `features/` | `FeatureFieldSpec`、`FeatureSetSpec`，JSON schema 存读 |
| `feature_engine.py` | `features/` | `BaseFeatureEngine` Protocol + `FeatureEngineBase` ABC |
| `feature_registry.py` | `features/` | `@register_feature_engine` 装饰器 + `build_feature_engine` 工厂 |
| `feature_store.py` | `features/` | `OnlineFeatureStore`（ring buffer）+ `OfflineFeatureStore`（Parquet） |
| `feature_pipeline.py` | `features/` | `FeaturePipeline`，编排 N 个引擎 |
| `interfaces.py` | `features/` | `StrategyRuntimeContext`，Mode B 信号引擎的上下文包 |
| `vwm_adapter.py` | `features/` | `VwmBarFeatureEngine`，适配现有 `VwmFeatureEngine` |
| `feature_recorder.py` | `features/` | `FeatureRecorder`，session 级别的 `OfflineFeatureStore` 封装 |
| `feature_cache.py` | `features/` | `FeatureQueryCache`，LRU 缓存 `OfflineFeatureStore` 查询结果 |
| `feature_joiner.py` | `features/` | `FeatureJoiner`，将特征 DataFrame 与行情 DataFrame 按时间戳 join |
| `feature_checkpoint.py` | `features/` | `FeatureCheckpointManager`，保存/恢复 `FeaturePipeline` 状态 |
| `feature_dataset.py` | `ml/` | `FeatureDatasetSpec` + `load_feature_dataset()`，训练数据加载 |
| `inference_context.py` | `ml/` | `ModelInferenceContext`，在线推理的特征向量组装 |

### 各组件详细职责

**`FeatureEvent`**：系统中所有特征数据的最小单元。它是 frozen dataclass，不依赖 Nautilus Cython，可以在任意 Python 环境中使用。online 路径每 bar 创建一个对象；offline 路径通过 `to_row()` / `from_row()` 批量转换为 DataFrame。

**`FeatureSetSpec`**：一个特征集合的正式 schema 描述，包含字段名、类型、nullable、版本号、输入事件类型等。通过 `save()` / `load()` 以 JSON 格式持久化到 `features/schemas/` 目录，供训练脚本和推理脚本直接读取，无需反向解析 Parquet 文件列名。

**`FeatureEngineBase`**：所有特征引擎的便利基类。子类只需实现 `update(event) -> FeatureEvent | None`；`warmup()` 和 `update_many()` 有默认实现。`state_dict()` / `load_state_dict()` 支持断点续跑。

**`OnlineFeatureStore`**：以 `(instrument_id, feature_set_id)` 为键，为每对键维护一个 `deque`（ring buffer）。热路径完全在内存中运行，无任何文件 I/O。`get_latest()` 是信号引擎在每个 bar 调用的方法。

**`OfflineFeatureStore`**：缓冲 `FeatureEvent` 至阈值（默认 1000 条）后批量写出 Parquet 文件。文件名编码时间范围（`{start_ts}-{end_ts}.parquet`），`query()` 可根据路径名快速跳过不相关文件再做行级过滤。

**`FeaturePipeline`**：编排多个特征引擎，将一个 market event 广播给所有引擎，收集 `FeatureEvent` 列表，然后分发给 `OnlineFeatureStore` 和 `OfflineFeatureStore`。也是唯一负责打 `is_warmup=True` 标记的地方。

---

## 4. Online 低延迟路径与 Offline 持久化路径

系统中有两条并行的特征数据路径，面向不同的下游消费者：

### 路径对比

| 维度 | Online 路径（热路径） | Offline 路径（冷路径） |
|------|----------------------|----------------------|
| **目的** | 实时信号决策 | 历史特征持久化 |
| **数据形式** | `FeatureEvent` Python 对象 | Parquet 文件（via DataFrame） |
| **存储** | `OnlineFeatureStore`（内存 deque） | `OfflineFeatureStore`（磁盘） |
| **DataFrame 创建** | 绝不创建 | 仅在 `flush()` 时批量转换 |
| **延迟** | O(1)，纯内存操作 | 批量写出，`flush_threshold=1000` |
| **消费者** | `SignalEngine.update(event, context=...)` | 训练脚本、`FeatureJoiner`、`load_feature_dataset()` |
| **warmup 事件** | 存入 ring buffer（最新快照供第一个 live bar 使用） | 写入 Parquet 但 `query(include_warmup=False)` 默认排除 |

### 数据流图

```
MarketEvent (BarInput)
        │
        ▼
FeaturePipeline._process_event()
        │
        ├─── engine.update(event) → FeatureEvent
        │           ↓ (if is_warmup, stamp flag)
        │
        ├─── OnlineFeatureStore.put(event)
        │       └─ deque[(instrument_id, feature_set_id)]
        │               ↓ get_latest()
        │           SignalEngine.update(event, context=context)
        │
        └─── OfflineFeatureStore.append(event)
                └─ buffer[...] → flush() when ≥ 1000
                        └─ pd.DataFrame → Parquet 写出
                                ↓ (at session end or threshold)
                        features/offline/{feature_set_id}/{instrument_id}/{start_ts}-{end_ts}.parquet
```

### 输出目录结构

```
outputs/datasets/{dataset_id}/
  raw/                          ← 原始行情（已有）
  normalized/                   ← 归一化 bar（已有）
  features/
    schemas/
      vwm_features_v1_1.json    ← FeatureSetSpec JSON
    offline/
      vwm_features_v1/
        BTCUSDT-PERP_BINANCE/
          {start_ts}-{end_ts}.parquet
  runs/
    {run_id}/
      signals.csv
      orders.csv
      run_info.json
```

---

## 5. 历史特征、warmup 特征、live 特征的统一

三类特征通过同一个 `FeatureEvent` 数据类型表示，通过 `is_warmup` 字段和 `FeaturePipeline.warmup()` / `update()` 的调用时序区分：

### 分类说明

| 类型 | 产生方式 | `is_warmup` | 是否写入 `OnlineFeatureStore` | 是否写入 `OfflineFeatureStore` | 是否用于训练 |
|------|---------|------------|-------------------------------|-------------------------------|------------|
| **历史特征** | `pipeline.update_many(historical_bars)` | False | 是 | 是 | 是 |
| **Warmup 特征** | `pipeline.warmup(warmup_bars)` | True | 是（用于初始化 ring buffer） | 是（但 `query()` 默认排除） | 否 |
| **Live 特征** | `pipeline.update(live_bar)` | False | 是 | 是 | 是 |

### 统一机制：`is_warmup` 标记

`FeaturePipeline` 在 `warmup()` 方法执行期间将内部标志 `_warmup_mode` 置为 True。`_process_event()` 在此期间产生的所有 `FeatureEvent` 都会通过 `dataclasses.replace(fe, is_warmup=True)` 被打上 warmup 标记，而不修改原始对象（frozen dataclass）：

```python
# FeaturePipeline._process_event() 内部
if self._warmup_mode and not fe.is_warmup:
    fe = replace(fe, is_warmup=True)
```

### 训练时的 Point-in-Time 正确性

训练脚本调用 `OfflineFeatureStore.query(include_warmup=False)`（默认值），自动排除 warmup 行。`load_feature_dataset()` 也默认设置 `include_warmup=False`：

```python
spec = FeatureDatasetSpec(
    feature_store_path="outputs/datasets/my_dataset/features",
    feature_set_ids=["vwm_features_v1"],
    instruments=["BTCUSDT-PERP.BINANCE"],
    include_warmup=False,  # 默认，确保 point-in-time 正确
)
df = load_feature_dataset(spec)
```

---

## 6. 特征如何复用到多个策略

### Mode A vs Mode B

系统支持两种信号引擎运行模式：

**Mode A（向后兼容，VWM 引擎）**：信号引擎内部自行计算特征，`FeaturePipeline` 不参与决策逻辑。现有 `VolumeWeightedMomentumShortSignalEngine` 使用此模式，无需任何改动。

```python
# Mode A：引擎自行计算特征
result = signal_engine.update(bar, position=position, bars_since_entry=n)
```

**Mode B（特征外化，未来引擎）**：特征计算集中在 `FeaturePipeline`，信号引擎只做决策逻辑。多个策略可以订阅同一个 `FeaturePipeline`。

```python
# Mode B：特征外化，策略共享
feature_events = pipeline.update(bar_event)
context = StrategyRuntimeContext(
    event=bar_event,
    features=pipeline.get_latest_features(instrument_id),
    position=current_position,
    bars_since_entry=n,
)
result_a = strategy_a.update(bar_event, context=context)
result_b = strategy_b.update(bar_event, context=context)  # 复用同一批特征
```

### 复用的实现机制

`FeaturePipeline.get_latest_features(instrument_id)` 从 `OnlineFeatureStore` 读取该 instrument 所有特征集的最新 `FeatureEvent`，返回 `dict[feature_set_id, FeatureEvent]`。`StrategyRuntimeContext` 持有这个 dict，并提供便捷访问接口：

```python
# 从 context 中访问特征
vwm_value = context.get_value("vwm_features_v1", "vwm")
all_vwm_features = context.get_feature_values("vwm_features_v1")
# 兼容旧式 dict.get()
position = context.get("position")
```

### 特征共享示意

```
                    FeaturePipeline
                         │
              ┌──────────┼──────────┐
              ▼          ▼          ▼
       VwmEngine   AtrEngine   OrderflowEngine
              │          │          │
              └──────────┴──────────┘
                         │
                  OnlineFeatureStore
                  {("BTCUSDT", "vwm_features_v1"): deque[...]}
                  {("BTCUSDT", "atr_v1"):          deque[...]}
                         │
              ┌──────────┼──────────┐
              ▼          ▼          ▼
         策略 A        策略 B       策略 C
    (context.features 相同)
```

---

## 7. 特征如何流向未来训练与推理

### 训练路径

`OfflineFeatureStore` → `load_feature_dataset()` → 训练脚本

```python
from nautilus_ext.ml.feature_dataset import FeatureDatasetSpec, load_feature_dataset

spec = FeatureDatasetSpec(
    feature_store_path="outputs/datasets/btc_2024/features",
    feature_set_ids=["vwm_features_v1"],
    instruments=["BTCUSDT-PERP.BINANCE"],
    start=1_700_000_000_000,
    end=1_701_000_000_000,
    include_warmup=False,
)
df = load_feature_dataset(spec)
# df 包含列: ts_event, instrument_id, momentum, vwm, atr, prev_vwm, ...
model.fit(df[feature_cols], labels)
```

`FeatureDatasetSpec` 明确声明了要用哪些特征集、哪些 instrument、哪个时间范围，训练脚本完全不需要了解底层 Parquet 文件的路径结构。

### 推理路径

`OnlineFeatureStore` → `ModelInferenceContext.get_feature_vector()` → 模型调用

```python
from nautilus_ext.ml.inference_context import ModelInferenceContext

ctx = ModelInferenceContext(
    online_store=pipeline.online_store,
    feature_set_ids=["vwm_features_v1"],
    feature_order=["vwm_features_v1.vwm", "vwm_features_v1.atr", ...],
)

# 每个 bar 调用，纯内存操作
if ctx.is_ready("BTCUSDT-PERP.BINANCE"):
    vector = ctx.get_feature_vector("BTCUSDT-PERP.BINANCE")
    # vector = {"vwm_features_v1.vwm": 0.042, "vwm_features_v1.atr": 0.0015, ...}
    prediction = model.predict([list(vector.values())])
```

`feature_order` 参数保证每次推理的特征向量列顺序与训练时一致，即使将来新增了特征字段也不会悄悄打乱已有列的位置。

### 训练与推理的一致性保障

| 保障机制 | 描述 |
|---------|------|
| **相同 `FeatureSetSpec`** | 训练和推理使用同一个 JSON schema 文件，字段定义相同 |
| **相同引擎代码路径** | `VwmBarFeatureEngine.update()` 在训练回放和实时推理中执行完全相同的计算 |
| **`feature_order` 参数** | 推理端固定列序，防止字段顺序变化导致的静默错误 |
| **`include_warmup=False`** | 训练数据不含 warmup 特征，与生产推理语义一致 |

---

## 8. 如何减少数据类型转换

### Online 路径：严禁创建 DataFrame

这是整个 Feature Data Layer 最核心的设计规则。每个 bar 到达时，online 路径的对象链如下：

```
BarInput (Nautilus 对象)
    ↓ engine.update(event)
FeatureEvent (frozen dataclass, ~10 个 float/int/bool 字段)
    ↓ online_store.put(event)
deque[FeatureEvent] (in-memory ring buffer)
    ↓ get_latest()
FeatureEvent (直接被信号引擎读取)
```

整个热路径中，**没有任何 `pd.DataFrame()` 调用，没有任何 `.to_dict()` / `.from_dict()` 格式转换**。`FeatureEvent` 是一个轻量的 frozen dataclass，访问 `event.values["vwm"]` 就是一次 dict 查找，代价极低。

**历史对比：Pandas-in-hot-path 反模式**

DataHander（参考审计对象）在每次 day task 中通过 Pandas 做多次格式转换（DataFrame → dict → DataFrame），这是明显的性能瓶颈，尤其在高频 bar 场景下会累积显著延迟。我们的 online 路径完全规避了这个问题。

### Offline 路径：仅在 flush 时做一次转换

```python
# OfflineFeatureStore.flush() — 唯一的 DataFrame 创建点
rows = [e.to_row() for e in self._buffer]   # list comprehension，无 Pandas
df = pd.DataFrame(rows)                      # 一次性批量构建 DataFrame
df.to_parquet(dest, index=False, engine="pyarrow")
```

`to_row()` 只是将 `FeatureEvent` 的字段 flatten 成一个普通 Python dict，不依赖 Pandas。只有在最终的 `pd.DataFrame(rows)` 调用时才会触发 Pandas 的内存分配，且此时是对整个 buffer（~1000 条）做一次批量操作，而非每条事件一次。

### 类型转换汇总表

| 位置 | 转换类型 | 频率 | 说明 |
|------|---------|------|------|
| `engine.update(event)` → `FeatureEvent` | 无转换（直接赋值） | 每 bar | 热路径，零分配 |
| `online_store.put(event)` | 无转换（append to deque） | 每 bar | 热路径，零分配 |
| `offline_store.append(event)` | 无转换（append to list） | 每 bar | 缓冲，延迟处理 |
| `offline_store.flush()` | `FeatureEvent → dict → DataFrame` | 每 ~1000 bar | 批量，一次性 |
| `online_store.get_latest()` | 无转换（读取 deque[-1]） | 每 bar | 热路径，零分配 |
| `load_feature_dataset()` | `Parquet → DataFrame` | 训练时 | 冷路径，可接受 |

---

## 9. 如何加新特征（步骤）

以添加一个新的"资金流量特征集" `moneyflow_v1` 为例：

### 步骤 1：创建特征引擎文件

创建 `nautilus_ext/features/moneyflow_engine.py`：

```python
from nautilus_ext.features.feature_engine import FeatureEngineBase
from nautilus_ext.features.feature_event import FeatureEvent
from nautilus_ext.features.feature_registry import register_feature_engine
from nautilus_ext.features.feature_schema import FeatureFieldSpec, FeatureSetSpec
from nautilus_ext.strategies.interfaces.input_types import BarInput

MONEYFLOW_SCHEMA_V1 = FeatureSetSpec(
    feature_set_id="moneyflow_v1",
    version="1",
    input_types=["bar"],
    output_features=[
        FeatureFieldSpec("net_flow", "float", description="Net money flow"),
        FeatureFieldSpec("flow_ratio", "float", description="Flow ratio to ATR"),
    ],
    required_history=1,
    point_in_time_safe=True,
    description="Money flow features derived from bar volume and price.",
    owner="your_name",
)

@register_feature_engine("moneyflow_v1")
class MoneyflowEngine(FeatureEngineBase):
    @property
    def name(self) -> str:
        return "moneyflow_v1"

    @property
    def schema(self) -> FeatureSetSpec:
        return MONEYFLOW_SCHEMA_V1

    def reset(self) -> None:
        self._prev_close = None

    def update(self, event) -> FeatureEvent | None:
        if not isinstance(event, BarInput):
            return None
        net_flow = (event.close - event.open) * event.volume
        # ... 计算其他特征
        return FeatureEvent(
            ts_event=event.ts_event,
            instrument_id=event.instrument_id,
            feature_set_id="moneyflow_v1",
            feature_version="1",
            values={"net_flow": net_flow, "flow_ratio": net_flow / (event.high - event.low + 1e-9)},
            source_event_type="bar",
        )

    def state_dict(self) -> dict:
        return {"prev_close": self._prev_close}

    def load_state_dict(self, state: dict) -> None:
        self._prev_close = state.get("prev_close")
```

### 步骤 2：确认注册触发

在 `feature_registry.py` 的 `_ensure_builtin_engines_registered()` 中添加 import，或在使用前直接 import 该模块：

```python
# feature_registry.py
def _ensure_builtin_engines_registered() -> None:
    try:
        import nautilus_ext.features.vwm_adapter       # noqa: F401
        import nautilus_ext.features.moneyflow_engine  # noqa: F401  ← 新增
    except Exception:
        pass
```

### 步骤 3：在 Pipeline 配置中使用

```python
from nautilus_ext.features.feature_pipeline import FeaturePipeline
from nautilus_ext.features.feature_registry import build_feature_engine
from nautilus_ext.features.feature_store import OnlineFeatureStore, OfflineFeatureStore

pipeline = FeaturePipeline(
    feature_engines=[
        build_feature_engine("vwm_features_v1"),
        build_feature_engine("moneyflow_v1"),   # ← 新特征集
    ],
    online_store=OnlineFeatureStore(),
    offline_store=OfflineFeatureStore("outputs/datasets/my_ds/features"),
)
```

### 步骤 4：保存 schema 文件（可选，推荐用于训练）

```python
from nautilus_ext.features.moneyflow_engine import MONEYFLOW_SCHEMA_V1
MONEYFLOW_SCHEMA_V1.save("outputs/datasets/my_ds/features/schemas/moneyflow_v1_1.json")
```

### 步骤 5：确认现有 Pipeline 和 Runner 无需修改

`FeaturePipeline` 通过 `BaseFeatureEngine` 协议迭代所有引擎，新引擎自动被广播到每个 market event，**不需要修改任何 runner 或 pipeline 代码**。

---

## 10. 如何加新策略（步骤）

以添加一个 Mode B 的新动量策略 `AlphaDecayStrategy` 为例：

### 步骤 1：实现信号引擎

```python
from nautilus_ext.strategies.interfaces.base_signal_engine import BaseSignalEngine
from nautilus_ext.strategies.interfaces.output_types import SignalResult
from nautilus_ext.features.interfaces import StrategyRuntimeContext

class AlphaDecaySignalEngine(BaseSignalEngine):
    def update(self, event, context: StrategyRuntimeContext | None = None, **kwargs) -> SignalResult:
        if context is None:
            return SignalResult(reason="no context")
        
        vwm = context.get_value("vwm_features_v1", "vwm")
        flow = context.get_value("moneyflow_v1", "net_flow")
        
        if vwm is None or flow is None:
            return SignalResult(reason="features not ready")
        
        if vwm > 0 and flow > 0 and context.position == 0:
            return SignalResult(entry_side="buy", reason="alpha_decay_long")
        
        return SignalResult(reason="no_signal")
```

Mode B 策略不计算任何特征，只从 `StrategyRuntimeContext.features` 中读取已计算好的特征值。

### 步骤 2：注册策略（如使用 strategy registry）

```python
from nautilus_ext.strategies.strategy_registry import register_strategy

@register_strategy("alpha_decay_v1")
class AlphaDecaySignalEngine(BaseSignalEngine):
    ...
```

### 步骤 3：在 Runner 中装配 FeaturePipeline + 新策略

```python
pipeline = FeaturePipeline(
    feature_engines=[
        build_feature_engine("vwm_features_v1"),
        build_feature_engine("moneyflow_v1"),
    ],
    online_store=OnlineFeatureStore(),
)

strategy = AlphaDecaySignalEngine()

# 在 bar loop 中：
for bar in bar_stream:
    feature_events = pipeline.update(bar)
    context = StrategyRuntimeContext(
        event=bar,
        features=pipeline.get_latest_features(instrument_id),
        position=current_position,
        bars_since_entry=bars_since_entry,
    )
    result = strategy.update(bar, context=context)
```

### 步骤 4：现有策略（Mode A）无需修改

Mode A 策略（如 `VolumeWeightedMomentumShortSignalEngine`）直接调用 `engine.update(bar, position=..., bars_since_entry=...)` 即可，`FeaturePipeline` 的引入不影响任何现有策略的代码。

---

## 11. DataHander 参考审计

### DataHander 是什么

DataHander 是一个面向 A 股市场的数据管道系统，接入 RiceQuant（米筐）和 TransendDataBase（超宇）两个数据源。其核心结构为一个带 5 个 Mixin 的 `DataHandler` 类：

| Mixin | 职责 |
|-------|------|
| `BaseMixin` | 数据库连接、基础查询 |
| `DiskMixin` | Hive 分区 Parquet 读写 |
| `MetaMixin` | 股票元数据管理 |
| `VendorMixin` | 数据源适配（RiceQuant / TransendDataBase） |
| `CalcorMixin` | 特征派生，Ray 并行计算，Polars/PyArrow pipeline |

存储采用 Hive 分区 Parquet + 分区级 LRU 缓存，计算采用 Ray 进行跨 symbol 并行化。

### 已借鉴的内容

| DataHander 机制 | 我们的对应实现 | 说明 |
|----------------|-------------|------|
| 分区键 LRU 缓存（`DataPartitionCache`） | `OnlineFeatureStore` ring buffer per `(instrument_id, feature_set_id)` + `FeatureQueryCache` | 采用更轻量的 deque 而非全局 LRU，key 语义相同 |
| 增量计算模式（diff existing vs schedule → skip already-computed） | `OfflineFeatureStore.query()` + append/flush 设计 | 通过时间戳范围文件名（`{start_ts}-{end_ts}.parquet`）快速判断是否需要重算 |
| 两级过滤（目录分区 + 行过滤） | `OfflineFeatureStore.query()` 路径过滤 + 行级过滤 | 先在文件路径层面过滤 `feature_set_id`/`instrument_id`，再做行级时间戳过滤 |
| Schema 兼容性检查理念 | `FeatureSetSpec` + 版本化 JSON schema 文件 | 字段变更触发版本号变更，schema 文件存储于 `features/schemas/` |
| 批量文件写出（避免 per-row 小文件） | `OfflineFeatureStore.flush_threshold=1000` | 缓冲满 1000 条后批量写出单个 Parquet 文件 |

### 未借鉴的内容及原因

| DataHander 特性 | 未借鉴的原因 |
|----------------|------------|
| **Windows 硬编码路径**（`cfgloader.py` 中 `D:\mine\...`） | 不可移植。我们使用 `pathlib.Path` + 相对于 `outputs/` 的路径，跨平台兼容 |
| **Ray 并行化**（用于跨 symbol 特征派生） | 对单机单 symbol 交易场景引入过重依赖；`FeaturePipeline` 采用 O(engines) 同步迭代已经足够 |
| **`sys.path` 污染**（`lbwloader.py` 动态修改 `sys.path`） | 违反 Python 打包标准；我们使用 `@register_feature_engine` 装饰器 + 显式 import |
| **Pandas 在热路径上**（每次 day task 多次格式转换） | DataFrame 分配代价在高频 bar 下不可接受；我们的 online 路径严禁创建 DataFrame |
| **UUID 命名的 Parquet 文件** | 无法通过文件名判断时间范围，查询时必须打开每个文件。我们使用 `{start_ts}-{end_ts}.parquet` 命名 |
| **MATLAB I/O**（`h5py`/`scipy.io`） | 与交易系统无关，不引入 |
| **纯批处理设计**（无实时语义） | DataHander 没有 `update(event)` 增量接口；我们的 `FeatureEngineBase.update()` 是核心方法 |
| **魔术字符串模块注册**（基于字符串动态 import 任意模块） | 不可预测、难以测试；我们使用显式 `@register_feature_engine("name")` 装饰器 |

---

## 12. 当前实现边界与未来 TradingNode 接入

### 当前实现边界

当前 Feature Data Layer 在 **paper live runner**（ccxt polling 模式）下完整运行，不依赖 Nautilus TradingNode：

```
当前架构（已实现）:
  CcxtPollingBarFeed
      ↓ BarInput
  FeaturePipeline.update(event)
      ↓ FeatureEvent list
  OnlineFeatureStore (ring buffer)
      ↓ get_latest_features()
  StrategyRuntimeContext
      ↓
  SignalEngine.update(event, context=context)
      ↓ SignalResult
  DryRunExecutionRecorder → orders.csv
  FeatureRecorder → OfflineFeatureStore → Parquet
```

`StrategyRuntimeContext.portfolio_snapshot` 字段已预留，但当前 paper live runner 不填充（值为 `None`），留待 TradingNode 接入时使用。

### 未来 TradingNode 接入方式

当项目迁移到 Nautilus `TradingNode` 正式实盘时，Feature Data Layer 的接入点如下：

**数据引擎回调（Data Engine Callback）**：在 Nautilus `Strategy.on_bar()` 回调中调用 `FeaturePipeline.update(bar_event)`，返回的 `FeatureEvent` 列表立即推入 `OnlineFeatureStore`。

```python
# 未来 TradingNode Strategy 示例（概念代码）
class MyNautilusStrategy(Strategy):
    def on_bar(self, bar: Bar) -> None:
        bar_input = BarInput.from_nautilus(bar)          # 适配层
        self._pipeline.update(bar_input)                 # 特征计算
        context = StrategyRuntimeContext(
            event=bar_input,
            features=self._pipeline.get_latest_features(str(bar.bar_type.instrument_id)),
            position=self._get_position(),
            portfolio_snapshot=self._get_portfolio_snapshot(),  # 填充真实组合状态
        )
        result = self._signal_engine.update(bar_input, context=context)
        self._execute_signal(result)
```

**关键适配点**：

| 当前（paper live） | 未来（TradingNode） |
|-----------------|-------------------|
| `CcxtPollingBarFeed` 产生 `BarInput` | Nautilus `DataEngine` 产生 `Bar`，需要适配为 `BarInput` |
| `DryRunExecutionRecorder` 记录 `OrderIntent` | Nautilus `Strategy.submit_order()` 直接提交订单 |
| `portfolio_snapshot=None` | 从 Nautilus `Portfolio` / `Cache` 获取真实持仓、P&L、保证金 |
| session 级别 `FeatureRecorder.flush()` | 在 `Strategy.on_stop()` 中调用 `pipeline.flush()` 确保最终写出 |
| 手动管理 warmup | 利用 Nautilus `Strategy.on_start()` + 历史数据回放接口预热 |

**不需要修改的部分**：`FeatureEvent`、`FeatureSetSpec`、`FeatureEngineBase`、`OnlineFeatureStore`、`OfflineFeatureStore`、`FeaturePipeline` — 这些组件均不依赖 Nautilus Cython，可直接在 TradingNode 的 Strategy 回调中使用。

**已隔离的 Nautilus 依赖**：仅 `VwmBarFeatureEngine`（通过 `VwmFeatureEngine` → Nautilus EMA/ATR 指标）依赖 Nautilus Cython。其余 Feature Data Layer 代码完全是纯 Python，可在任何环境中测试和运行。

---

*文档生成日期：2026-06-03*  
*对应代码版本：nautilus_ext develop 分支，commit a1b7c756ef*
