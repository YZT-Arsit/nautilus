# 特征计算更新链路（Feature Update Flow）

## 1. 为什么特征更新是难点

量化系统中"特征"有三个维度的要求，彼此存在张力：

| 要求 | 难点 |
|------|------|
| **增量更新（online）** | 每根 Bar 到来时必须在毫秒级内更新，不能创建 DataFrame |
| **历史复现（offline）** | 历史计算必须与实时计算结果完全一致，不能两套逻辑 |
| **点时间正确（point-in-time safe）** | warmup 阶段产生的特征不能混入训练数据，否则引入前瞻偏差 |

此外还有工程要求：
- 新策略/新特征不应修改 runner 主流程
- 多策略共用同一个特征集时，特征只应计算一次
- 特征要能持久化为 Parquet，供模型训练直接读取

传统做法（在 signals.csv 的 debug 字段里顺带记录特征）满足不了以上任何一条：特征计算依赖信号引擎的内部实现，每个策略各算各的，Parquet 格式缺失，训练数据必须从 CSV 反推。

---

## 2. 为什么特征不能只存在 signals.csv / debug 字段

`signals.csv` 是**信号日志**，其中的 `debug_json` 字段记录的是调试快照：

- 行数 = 信号数，不是特征数（有些 bar 可能不产生信号）
- 格式由每个信号引擎自定义，无法保证跨引擎统一
- 没有版本号，schema 变更后历史数据无法自动识别
- 字段随 `SignalResult.debug` 变化而变化，训练管线无法稳定依赖
- 无 `is_warmup` 标记，warmup 阶段的"特征"和正式特征混在一起

**正式特征存储路径：**

```
outputs/features/
    schemas/{feature_set_id}_{version}.json   # 有版本的 schema 定义
    offline/{feature_set_id}/{instrument_id}/{start_ts}-{end_ts}.parquet
```

`signals.csv` 中的 `feature_set_ids` / `feature_event_ts` 两列是**索引指针**，
用于调试时快速定位对应的 FeatureStore parquet 行，而不是特征的权威来源。

---

## 3. MarketEvent → FeatureEngine.update → FeatureEvent

```
MarketEvent (BarInput / TradeTickInput / ...)
    │
    ▼
FeatureEngine.update(event) -> FeatureEvent | None
    │
    ├── 若 event 类型不匹配 → 返回 None（忽略）
    │
    └── 若匹配 → 更新内部增量状态（EMA、ATR、momentum …）
                  构造 FeatureEvent（frozen dataclass）
                  返回 FeatureEvent
```

**关键约束：**
- `update(event)` 是热路径，**禁止在此方法内创建 DataFrame**
- 一次调用只处理一个事件，返回一个 FeatureEvent（或 None）
- FeatureEvent 是 frozen dataclass，不可变，天然线程安全
- `update_many(events)` 默认逐条调用 `update`，保证历史和 live 路径完全一致

**FeatureEvent 字段：**

```python
@dataclass(frozen=True)
class FeatureEvent:
    ts_event: int                # 毫秒 POSIX 时间戳（来自 bar）
    instrument_id: str           # 如 "BTCUSDT-PERP.BINANCE"
    feature_set_id: str          # 如 "vwm_features_v1"
    feature_version: str         # 如 "1"
    values: dict[str, float | int | bool | str | None]  # 特征值
    is_warmup: bool              # True = warmup 阶段，训练时排除
    source_event_type: str | None  # "bar" / "trade_tick" / …
```

---

## 4. FeaturePipeline 如何协调多个 FeatureEngine

```python
pipeline = FeaturePipeline(
    feature_engines=[VwmBarFeatureEngine()],
    online_store=OnlineFeatureStore(),
    offline_store=OfflineFeatureStore("outputs/features"),
)
```

内部流程（每次 `pipeline.update(event)`）：

```
for engine in engines:
    fe = engine.update(event)
    if fe is None:
        continue
    if warmup_mode:
        fe = replace(fe, is_warmup=True)   # dataclasses.replace，不可变
    online_store.put(fe)       # 热路径，O(1)，无 I/O
    offline_store.append(fe)   # 仅写 buffer，不触发 I/O
```

`flush()` 时才把 buffer 批量转为 DataFrame → Parquet：

```
offline_store.flush()
    → rows = [e.to_row() for e in buffer]
    → df = pd.DataFrame(rows)
    → df.groupby([instrument_id, feature_set_id])
        → each group → parquet_path → df.to_parquet()
    → buffer.clear()
```

---

## 5. OnlineFeatureStore 和 OfflineFeatureStore 各做什么

### OnlineFeatureStore（实时热路径）

- 数据结构：`dict[(instrument_id, feature_set_id)] → deque[FeatureEvent]`（有界环形缓冲区）
- 写入：`put(event)` — O(1)，无文件 I/O
- 读取：`get_latest(instrument_id, feature_set_id)` — O(1)，供策略每根 bar 调用
- 功能：保留最近 N 条（默认 500），按时间窗口查询
- 生命周期：进程内存，重启后清空

### OfflineFeatureStore（持久化路径）

- 数据结构：内存 buffer + Parquet 文件
- 写入：`append(event)` 写 buffer；`flush()` 批量落盘（防止每根 bar 产生一个小文件）
- 读取：`query(instrument_id, feature_set_id, start, end, include_warmup=False)`
- 目录结构：

```
base_path/
    schemas/{feature_set_id}_{version}.json
    offline/{feature_set_id}/{safe_instrument_id}/{start_ts}-{end_ts}.parquet
```

- `include_warmup=False` 默认排除 warmup 行 → 保证训练数据点时间正确
- 去重：按 (ts_event, instrument_id, feature_set_id) 排重（覆盖重复写入场景）

---

## 6. 历史、warmup、polling live 如何共用同一套 update 逻辑

三条路径都调用同一个 `FeatureEngine.update(event)`：

```
历史批量路径（BacktestRunner）
    bars = data_connector.prepare_data()   # Nautilus Bar 对象
    bar_inputs = [convert(b) for b in bars]
    pipeline.update_many(bar_inputs)       # 内部逐条 update
    pipeline.flush()                       # 落盘

warmup 路径（paper_live_runner / pipeline.warmup）
    pipeline.warmup(warmup_bars)
        → _warmup_mode = True
        → for event in bars: _process_event(event)
            → fe.is_warmup = True （replace，不可变）
            → online_store.put(fe)   # warmup 特征进入在线存储
            → offline_store.append(fe) # 也进 buffer，query 时默认排除
        → _warmup_mode = False

polling live 路径（paper_live_runner._process_bar）
    feature_events = pipeline.update(bar_input)   # _warmup_mode=False
    context = StrategyRuntimeContext(
        features={fe.feature_set_id: fe for fe in feature_events},
        ...
    )
    result = signal_engine.update(bar, context=context)
```

warmup 后继续 live 的状态连续性：
- `FeatureEngine` 内部状态（EMA、ATR 等）在 warmup 和 live 之间**不重置**
- warmup 结束时 `_warmup_mode` 切回 False，后续 `update` 产生 `is_warmup=False`
- 第一根 live bar 的特征正确继承 warmup 建立的历史窗口

---

## 7. 特征如何被策略复用

### Mode A（向后兼容，策略内部计算）

```python
result = signal_engine.update(bar, position=0, bars_since_entry=0)
# 引擎内部调用 self.features.update(bar)，不使用外部特征
```

### Mode B（外部特征，策略读取 context）

```python
feature_events = pipeline.update(bar)
context = StrategyRuntimeContext(
    event=bar,
    features={"vwm_features_v1": feature_events[0]},
    position=0,
)
result = signal_engine.update(bar, context=context)
# 引擎检测 context.get_feature_values("vwm_features_v1") 是否有值
# 有 → 使用外部特征（_VwmFeaturesFromContext 适配器）
# 无 → fallback Mode A（内部 VwmFeatureEngine.update(bar)）
```

**多策略共用同一个特征集：**

```python
pipeline = FeaturePipeline([VwmBarFeatureEngine()])  # 只运行一次
feature_events = pipeline.update(bar)
context = StrategyRuntimeContext(features=...)

result_a = strategy_a.update(bar, context=context)   # 读 vwm_features_v1
result_b = strategy_b.update(bar, context=context)   # 同一个 FeatureEvent，零额外计算
```

---

## 8. 特征如何被模型训练 / 推理复用

### 训练（离线）

```python
from nautilus_ext.ml.feature_dataset import FeatureDatasetSpec, load_feature_dataset

spec = FeatureDatasetSpec(
    feature_store_path="outputs/features",
    feature_set_ids=["vwm_features_v1"],
    instruments=["BTCUSDT-PERP.BINANCE"],
    start=1_704_067_200_000,
    end=1_706_745_600_000,
)
df = load_feature_dataset(spec)   # 自动排除 is_warmup=True 行
# df 可直接传给 sklearn / lightgbm / torch
```

### 推理（在线）

```python
from nautilus_ext.ml.inference_context import ModelInferenceContext

inference_ctx = ModelInferenceContext(online_store, ["vwm_features_v1"])
if inference_ctx.is_ready("BTCUSDT-PERP.BINANCE"):
    vec = inference_ctx.get_feature_vector("BTCUSDT-PERP.BINANCE")
    # vec = {"vwm_features_v1.vwm": 2.5, "vwm_features_v1.atr": 1.0, ...}
    prediction = model.predict([list(vec.values())])
```

推理路径：OnlineFeatureStore → ModelInferenceContext → 模型，**全程无文件 I/O**。

---

## 9. 如何减少实时路径的数据类型转换

| 阶段 | 是否允许 DataFrame |
|------|------------------|
| `FeatureEngine.update(event)` | ❌ 禁止 |
| `OnlineFeatureStore.put(event)` | ❌ 禁止 |
| `OfflineFeatureStore.append(event)` | ❌ 仅写 buffer |
| `OfflineFeatureStore.flush()` | ✅ 批量转 DataFrame，可接受 |
| `OfflineFeatureStore.query()` | ✅ 读 Parquet，离线路径 |
| `FeatureJoiner.join_df()` | ✅ 离线分析用 |

实时路径的对象链：

```
BarInput (frozen dataclass, ~10 fields)
    ↓ engine.update()
FeatureEvent (frozen dataclass, values dict)
    ↓ online_store.put()
deque[FeatureEvent] (in-memory ring buffer)
    ↓ get_latest()
FeatureEvent.values (dict)
    ↓ StrategyRuntimeContext
dict[str, FeatureEvent]
    ↓ signal_engine.update(bar, context)
SignalResult
```

整条链路无 DataFrame 创建，无序列化/反序列化，无文件 I/O。

---

## 10. 各文件职责

| 文件 | 职责 |
|------|------|
| `features/feature_event.py` | FeatureEvent 数据类型；to_row/from_row 离线序列化 |
| `features/feature_schema.py` | FeatureSetSpec / FeatureFieldSpec schema 定义与版本化 |
| `features/feature_engine.py` | BaseFeatureEngine 协议 + FeatureEngineBase 默认实现 |
| `features/feature_registry.py` | 注册/构建 FeatureEngine（工厂模式，runner 无需硬编码引擎类） |
| `features/vwm_features.py` | VwmFeatureEngine 内部计算（momentum/EMA/ATR/crossover）|
| `features/vwm_adapter.py` | VwmBarFeatureEngine：将 VwmFeatureEngine 适配为 BaseFeatureEngine 协议，产生 FeatureEvent |
| `features/feature_pipeline.py` | 协调多个 engine；管理 warmup/update/flush；写 online/offline store |
| `features/feature_store.py` | OnlineFeatureStore（环形缓冲）+ OfflineFeatureStore（Parquet 批量写）|
| `features/interfaces.py` | StrategyRuntimeContext：策略读取特征的上下文对象 |
| `features/feature_checkpoint.py` | pipeline.state_dict/load_state_dict 的文件持久化包装 |
| `features/feature_cache.py` | query 结果的 LRU 缓存 |
| `features/feature_joiner.py` | OHLCV DataFrame 与 features DataFrame 的 ts_event join |
| `ml/feature_dataset.py` | load_feature_dataset：OfflineFeatureStore → 训练 DataFrame |
| `ml/inference_context.py` | ModelInferenceContext：OnlineFeatureStore → 模型输入向量 |
| `strategies/vwm_short_signals.py` | VolumeWeightedMomentumShortSignalEngine：Mode A（内部计算）+ Mode B（读 context.features）|
| `ccxt_live/paper_live_runner.py` | CcxtPaperLiveRunner：调用 pipeline.warmup → update → flush；传 feature_refs 给 recorder |
| `ccxt_live/signal_recorder.py` | SignalRecorder：记录信号日志；feature_set_ids/feature_event_ts 是索引指针，非特征存储 |
| `runners/backtest_runner.py` | NautilusBacktestRunner：支持可选 feature_pipeline 生成离线特征 Parquet |

---

## 11. 当前仍未完成的边界

| 边界 | 状态 | 说明 |
|------|------|------|
| Nautilus DataEngine 事件流接入 | ❌ 未实现 | BacktestRunner 的特征生成在 Nautilus 引擎运行**之前**批量完成，未接入实时事件循环 |
| 回测时特征与信号同步触发 | ❌ 未实现 | 当前是先批量生成特征再跑回测，未做逐 bar 同步 |
| BacktestRunner 调用 run_strategy 时的特征 context | ❌ 未实现 | Nautilus BaseBarStrategy 尚未接收 StrategyRuntimeContext |
| `metrics.json` 真实指标 | ❌ 未实现 | 当前输出 `{"available": false}`；PnL/Sharpe/最大回撤未从 BacktestEngine 提取 |
| `ccxt_live/session_reporter.py` | ❌ 文件不存在 | 实盘会话汇总功能缺失 |
| 多 instrument 特征隔离 | ✅ 已实现 | OnlineStore/OfflineStore 按 (instrument_id, feature_set_id) 分桶 |
| feature_pipeline state_dict 持久化 | ✅ 已实现 | FeatureCheckpointManager 封装文件 I/O |
| warmup 后继续 live 的状态连续性 | ✅ 已实现 | warmup_mode flag + frozen dataclass replace |
| 点时间正确（排除 warmup 训练数据）| ✅ 已实现 | is_warmup=True 行默认排除于 query 结果 |
| 热路径无 DataFrame | ✅ 已实现 | update 路径禁止创建 DataFrame，测试覆盖 |
