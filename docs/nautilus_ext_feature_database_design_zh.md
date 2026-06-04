# 特征数据库设计与实现

## 1. 为什么把历史行情、实时行情、特征视为一个数据库

传统量化系统中，数据往往散落在多个地方：

| 数据类型 | 传统位置 | 问题 |
|---------|---------|------|
| 历史行情 | CSV / Parquet 文件 | 无统一查询接口 |
| 实时行情 | 内存变量 | 重启即丢失，无法回溯 |
| 计算特征 | signals.csv 的 debug 字段 | 与策略耦合，无版本，无 schema |
| 信号决策 | signals.csv | 混合了输入特征和输出决策 |

这种散落结构导致：
- 多个策略各自重新计算同样的特征，浪费算力
- 特征格式随策略变动，训练数据无法稳定依赖
- warmup 阶段的特征和正式特征混在一起，引入前瞻偏差
- 新增特征需要修改 runner 主流程

**把这些数据统一视为一个"量化数据库"的核心收益：**

1. 特征只计算一次，多策略共享同一个 FeaturePipeline 输出
2. 存储结构有版本（feature_version）和 schema（FeatureSetSpec），变更可追溯
3. warmup 数据打 `is_warmup=True` 标记，训练数据集默认排除
4. 新增特征只需实现一个 FeatureEngine，不改 runner 主流程
5. 离线特征可以直接 join 到训练 DataFrame，不需要从 CSV 反推

---

## 2. 数据库逻辑结构

```
QuantDataLayer（量化数据层）
├── MetadataStore               — 暂未实现（instruments, contracts, trading_hours）
│
├── MarketDataStore             — 暂未实现（行情 Parquet 或 Nautilus catalog）
│
├── FeatureStore                — ✅ 当前核心实现
│     ├── OnlineFeatureStore   — 实时热路径（内存）
│     └── OfflineFeatureStore  — 离线持久化（Parquet + Manifest）
│
├── SignalStore                 — 暂未实现（signals.csv 是临时替代）
│     └── SignalRecorder       — 当前实现（CSV/Parquet per session）
│
├── OrderIntentStore            — 暂未实现（orders.csv 是临时替代）
│     └── DryRunExecutionRecorder — 当前实现
│
└── RuntimeCache                — 暂未实现（跨策略共享状态）
```

FeatureStore 是当前的核心模块，支持：
- 实时策略读取（OnlineFeatureStore.get_latest）
- 准实盘增量写入（OfflineFeatureStore.append + flush）
- 历史批量生成（BacktestRunner._run_feature_pipeline）
- 训练数据集读取（load_feature_dataset）
- 推理特征读取（ModelInferenceContext）

---

## 3. OnlineFeatureStore vs OfflineFeatureStore 的区别

| 维度 | OnlineFeatureStore | OfflineFeatureStore |
|------|-------------------|---------------------|
| 存储位置 | 进程内存 | Parquet 文件 |
| 写入延迟 | O(1)，无 I/O | append：O(1)；flush：批量 I/O |
| 读取延迟 | O(1) dict 查询 | 按 manifest 找文件，读 Parquet |
| 数据生命周期 | 进程内，重启后清空 | 持久化，跨进程可读 |
| 数据量 | 最近 N 条（window_size=500） | 无上限（按 Parquet 文件分批存储） |
| 主要用途 | 策略实时读取 latest | 训练数据集、离线分析 |
| 包含 warmup | 是（is_warmup 字段） | 是，但 query 默认排除 |

---

## 4. 内存中如何存（OnlineFeatureStore 详解）

### 4.1 双层索引结构

```
OnlineFeatureStore
  ├── _latest: dict[instrument_id][feature_set_id] → FeatureEvent
  │     最新快照，O(1) 直接查字典
  │     每次 put() 更新这个 dict
  │
  └── _buffers: dict[(instrument_id, feature_set_id)] → deque[FeatureEvent]
        历史窗口，maxlen=window_size（默认 500）
        每次 put() 追加到 deque
        满了自动淘汰最旧的事件（O(1) 摊销）
```

### 4.2 为什么 get_latest 是 O(1)

```python
def get_latest(self, instrument_id, feature_set_id):
    return self._latest.get(instrument_id, {}).get(feature_set_id)
```

两次 dict 查找，每次 O(1)。`_latest` 在每次 `put()` 时同步更新，始终保持最新值，不需要扫描 deque。

### 4.3 为什么 get_window 不扫全量

`_buffers[(iid, fsid)]` 是一个 `collections.deque(maxlen=window_size)`。

- 最多存 500 条，不会因为运行时间增长而无限膨胀
- `list(deque)` 转换 + 时间过滤 + 切片，最坏情况 O(500)，与历史总量无关
- 每个 (instrument_id, feature_set_id) 组合独立一个 deque，不同组合之间完全隔离

### 4.4 内存使用估算

以 `FeatureEvent(slots=True)` + 8 个 float 字段为例（Python 3.10+）：

- 无 slots：每个 `FeatureEvent` 约 360 字节（含 `__dict__`）
- 有 slots：每个 `FeatureEvent` 约 240 字节（减少约 30%）
- 500 条窗口 × 240 字节 ≈ 120KB / (instrument, feature_set) 组合
- 10 个交易对 × 3 个特征集 × 120KB ≈ 3.6MB — 可接受

---

## 5. 离线如何存（OfflineFeatureStore 详解）

### 5.1 目录结构

```
base_path/
    feature_manifest.json                   ← 文件级索引
    schemas/
        vwm_features_v1_1.json             ← FeatureSetSpec，含 schema 版本
    offline/
        vwm_features_v1/                   ← feature_set_id
            BTCUSDT-PERP_BINANCE/          ← safe_instrument_id（. → _）
                1704067200000-1704153600000.parquet   ← 时间戳范围编码到文件名
```

### 5.2 写入流程

```
append(event)
  → _buffer.append(event)     # 纯内存写，O(1)
  → if len(_buffer) >= flush_threshold: flush()   # 自动触发

flush()
  → rows = [e.to_row() for e in _buffer]    # 批量 flatten
  → df = pd.DataFrame(rows)                 # 一次性创建 DataFrame
  → for (iid, fsid), grp in df.groupby(...):
        grp.to_parquet(dest, engine="pyarrow")     # I/O 只在这里
        manifest.append_file_record(ManifestRecord(...))
  → manifest.save()           # 更新 feature_manifest.json
  → _buffer.clear()
```

热路径（append）只写内存，DataFrame 和文件 I/O 只在 flush 时发生一次。

### 5.3 Manifest 索引（feature_manifest.json）

Manifest 存储每个 Parquet 文件的元数据：

```json
[
  {
    "feature_set_id": "vwm_features_v1",
    "feature_version": "1",
    "instrument_id": "BTCUSDT-PERP.BINANCE",
    "start_ts": 1704067200000,
    "end_ts": 1704153600000,
    "row_count": 1440,
    "file_path": "/outputs/features/offline/vwm_features_v1/BTCUSDT-PERP_BINANCE/1704067200000-1704153600000.parquet",
    "created_at": "2024-01-02T00:00:00+00:00"
  }
]
```

---

## 6. get_latest 为什么快

```
路径对比：

旧版（deque[-1]）：
  _buffers.get(key)      # dict 查找 O(1)
  buf[-1]                # deque 末尾访问 O(1)
  总计：O(1)，但需要两次独立的 lookup

新版（_latest dict）：
  _latest.get(iid, {}).get(fsid)  # 两次 dict 查找 O(1)
  总计：O(1)，且结果始终是 Python dict 的直接引用，无 deque 访问

get_all_latest（新方法）：
  dict(self._latest.get(iid, {}))   # 一次 dict 查找 + 浅拷贝 O(k)
  k = 该 instrument 下的 feature_set 数量（通常 < 10）
```

对比：旧版的 `get_latest_features()` 需要遍历 `_buffers.keys()` 找到所有匹配 instrument_id 的 key，是 O(total_keys)。新版 `get_all_latest()` 直接查 `_latest[instrument_id]`，是 O(1)。

---

## 7. query 如何通过 manifest 减少文件扫描

```
无 manifest（旧行为）：
  offline_root.rglob("*.parquet")        # 扫描所有文件 O(all_files)
  → read + filter each file              # 读所有文件
  → concat → filter by ts_event         # 内存过滤

有 manifest（新行为）：
  manifest.find_files(                   # 内存中过滤 manifest 记录 O(records)
    feature_set_id=...,
    instrument_id=...,
    start=..., end=...,
  )
  → 只读匹配文件（重叠检查：r.start_ts <= query_end AND r.end_ts >= query_start）
  → concat → row-level filter（精确过滤边界）
```

**重叠检查逻辑**：manifest 中的文件时间范围 `[r.start_ts, r.end_ts]` 与查询范围 `[start, end]` 重叠当且仅当：
- `r.start_ts <= end`（文件开始不晚于查询结束）
- `r.end_ts >= start`（文件结束不早于查询开始）

不满足任一条件的文件直接跳过，不打开文件。

实际加速比：
- 1 年数据，每天一个文件，每次查 1 天 → 从读 365 个文件降到读 1 个文件
- manifest 本身 JSON 解析 < 1ms，瓶颈移到实际数据 I/O

---

## 8. 为什么实时路径不能频繁创建 DataFrame

DataFrame 创建有固定开销：

1. Python 内存分配（GC 压力）
2. pandas dtype 推断（每列扫描）
3. 数据从 Python 对象 → NumPy array 的类型转换

在每根 Bar 的热路径上，这些开销叠加：
- 1 分钟 K 线策略：每分钟 1 次 → 可接受
- 1 秒 K 线策略：每秒 1 次 → 开始明显
- Tick 策略：每 tick 1 次（可能每秒数百次）→ 完全不可接受

**实时路径上的对象类型约束：**

```
BarInput (frozen dataclass, ~10 float 字段)
    ↓ FeatureEngine.update(event)         ← 禁止 DataFrame
FeatureEvent (frozen dataclass, slots=True)
    ↓ OnlineFeatureStore.put(event)       ← 禁止 DataFrame
dict[instrument_id][feature_set_id] = fe  ← 纯 Python dict
    ↓ StrategyRuntimeContext
dict[str, FeatureEvent]
    ↓ signal_engine.update(bar, context)
SignalResult
```

整条热路径：无 DataFrame、无 Parquet I/O、无序列化/反序列化。

---

## 9. 为什么 flush 才转 Arrow/Parquet

```
append 阶段（每根 bar）：
  _buffer.append(event)        # O(1)，纯内存 list append
  不调用 to_row()
  不调用 pd.DataFrame()
  不调用 to_parquet()

flush 阶段（批量，每 N 根 bar 或手动触发）：
  rows = [e.to_row() for e in _buffer]   # 批量 flatten
  df = pd.DataFrame(rows)                # 一次 DataFrame 创建，分摊 N 次开销
  grp.to_parquet(...)                    # 一次文件 I/O，不是 N 次
```

把 N 次转换合并为 1 次，开销从 O(N × 单次转换) 降到 O(1 × 批量转换)。

flush_threshold 默认为 1000：每 1000 根 bar 写一次文件，平均每根 bar 的 I/O 开销 < 1/1000 次文件写入。

---

## 10. 特征如何进入训练

```python
from nautilus_ext.ml.feature_dataset import FeatureDatasetSpec, load_feature_dataset

spec = FeatureDatasetSpec(
    feature_store_path="outputs/features",    # OfflineFeatureStore 根目录
    feature_set_ids=["vwm_features_v1"],
    instruments=["BTCUSDT-PERP.BINANCE"],
    start=1_704_067_200_000,                  # 2024-01-01 (ms)
    end=1_706_745_600_000,                    # 2024-02-01 (ms)
    select_columns=["momentum", "vwm", "atr", "bull_setup", "bear_setup"],
    include_warmup=False,                     # 默认排除 warmup，确保点时间正确
)
df = load_feature_dataset(spec)
# df 包含 ts_event, instrument_id, feature_set_id, feature_version, is_warmup
# 以及 momentum, vwm, atr, bull_setup, bear_setup
# 可直接传给 sklearn / lightgbm / torch

X = df[["momentum", "vwm", "atr"]].values
y = df["label"].values  # label 需要自行 join（当前 label_spec 未实现）
```

**点时间正确性保证：**
- warmup 阶段（例如前 200 根 bar）生成的特征被标记 `is_warmup=True`
- `load_feature_dataset` 默认 `include_warmup=False`，这些行不进入训练集
- 避免了"用未来信息预测过去"的前瞻偏差

**manifest 加速查询：**
- `load_feature_dataset` 通过 `OfflineFeatureStore.query()` 读取
- `query()` 使用 manifest 只读时间范围匹配的文件，不扫描全目录

---

## 11. 特征如何进入推理

```python
from nautilus_ext.ml.inference_context import ModelInferenceContext

# 在 FeaturePipeline 每次 update() 后，OnlineFeatureStore 已持有最新特征
ctx = ModelInferenceContext(
    online_store=online_store,
    feature_set_ids=["vwm_features_v1"],
)

if ctx.is_ready("BTCUSDT-PERP.BINANCE"):
    vec = ctx.get_feature_vector("BTCUSDT-PERP.BINANCE")
    # vec = {
    #   "vwm_features_v1.momentum": 0.5,
    #   "vwm_features_v1.vwm": 2.3,
    #   "vwm_features_v1.atr": 1.1,
    #   ...
    # }
    prediction = model.predict([list(vec.values())])
```

推理路径：`OnlineFeatureStore.get_latest()` → dict → 模型输入向量。全程无文件 I/O。

---

## 12. 新策略/新特征如何低代码接入

### 新增一个特征集

只需新增一个 FeatureEngine 文件：

```python
# nautilus_ext/features/my_new_features.py
@register_feature_engine("my_features_v1")
class MyNewFeatureEngine(FeatureEngineBase):
    name = "my_features_v1"
    schema = FeatureSetSpec(feature_set_id="my_features_v1", ...)

    def update(self, event) -> FeatureEvent | None:
        # 计算逻辑，不创建 DataFrame
        return FeatureEvent(...)
```

然后在配置或 runner 中：

```python
pipeline = FeaturePipeline([
    VwmBarFeatureEngine(),     # 已有特征
    MyNewFeatureEngine(),       # 新增特征
])
```

**不需要修改：** FeaturePipeline、BacktestRunner、CcxtPaperLiveRunner、任何已有策略。

### 新增一个策略（使用已有特征）

```python
class MyStrategy(FeatureEngineBase):
    def update(self, bar, context=None, ...):
        # Mode B：从 context 读取 FeaturePipeline 已计算的特征
        vals = context.get_feature_values("vwm_features_v1") if context else None
        if vals is None:
            # Mode A fallback：自己计算
            vals = self.internal_engine.update(bar)
        # 只写决策逻辑，不重复计算特征
```

---

## 13. 各模块职责总表

| 模块 | 职责 | 当前状态 |
|------|------|---------|
| `feature_event.py` | FeatureEvent 不可变数据类；to_row/from_row | ✅ 完整，slots=True |
| `feature_schema.py` | FeatureSetSpec / FeatureFieldSpec schema 版本化 | ✅ 完整 |
| `feature_engine.py` | BaseFeatureEngine 协议 + FeatureEngineBase 默认实现 | ✅ 完整 |
| `feature_registry.py` | 注册/构建 FeatureEngine（工厂模式） | ✅ 完整 |
| `feature_manifest.py` | JSON 文件索引；find_files 时间范围重叠过滤 | ✅ 本轮新增 |
| `feature_store.py` | OnlineFeatureStore（双层索引）+ OfflineFeatureStore（manifest 集成） | ✅ 本轮优化 |
| `feature_pipeline.py` | 协调多引擎；get_latest_features O(1)；get_feature_window | ✅ 本轮优化 |
| `feature_cache.py` | query 结果 LRU 缓存 | ✅ 完整 |
| `feature_checkpoint.py` | state_dict/load_state_dict 文件持久化 | ✅ 完整 |
| `feature_joiner.py` | OHLCV DataFrame 与 features DataFrame 的 ts_event join | ✅ 完整 |
| `feature_recorder.py` | session 级 OfflineFeatureStore 包装 | ✅ 完整 |
| `interfaces.py` | StrategyRuntimeContext Mode B 上下文 | ✅ 完整 |
| `vwm_adapter.py` | VwmBarFeatureEngine：VwmFeatureSnapshot → FeatureEvent | ✅ 完整 |
| `ml/feature_dataset.py` | load_feature_dataset：select_columns + 点时间过滤 | ✅ 本轮优化 |
| `ml/inference_context.py` | ModelInferenceContext：OnlineStore → 模型输入向量 | ✅ 完整 |

---

## 14. 当前实现边界与未来优化

### ✅ 已实现

| 功能 | 说明 |
|------|------|
| 热路径无 DataFrame | update() 链路全部 frozen dataclass，测试覆盖（monkeypatch） |
| O(1) get_latest | `_latest` 双层 dict，不扫 deque |
| O(1) get_all_latest | `_latest[instrument_id]` 浅拷贝 |
| Manifest 文件索引 | JSON，flush 后自动写，query 优先使用 |
| 时间范围文件剪枝 | 重叠检查，只读匹配文件 |
| 批量 flush | append 到 buffer，flush 才写 Parquet |
| 多 flush 去重 | drop_duplicates on (ts_event, instrument_id, feature_set_id) |
| warmup 点时间隔离 | is_warmup=True 标记，query 默认排除 |
| select_columns | load_feature_dataset 支持按列读取 |
| 状态 checkpoint | FeatureCheckpointManager JSON 持久化 |
| Mode A/B 兼容 | VwmShortSignalEngine 支持内部计算和外部 context 两种模式 |

### ❌ 未实现（未来优化边界）

| 功能 | 说明 |
|------|------|
| PyArrow column selection | 当前 read_parquet 读全列再 select；可用 `columns=` 参数减少 I/O |
| Polars lazy scan | 大数据集可用 polars.scan_parquet 避免一次性加载到内存 |
| Parquet manifest | 当前 JSON manifest；数据量大时 Parquet manifest 查询更快 |
| 多 instrument 并行 flush | 当前串行 groupby flush；可按 instrument_id 并行写 |
| Nautilus DataEngine 实时接入 | BacktestRunner 当前是批量预计算特征，未接入 Nautilus 事件循环逐 bar 同步 |
| BacktestRunner 逐 bar feature context | 回测策略暂不接收 StrategyRuntimeContext（Mode B） |
| metrics.json 真实指标 | 当前输出 `{"available": false}`；PnL/Sharpe 未从 BacktestEngine 提取 |
| session_reporter.py | 实盘会话汇总文件不存在 |
| numpy ring buffer | 高频场景可用 numpy array ring buffer 替代 deque，避免 GIL |

---

## 15. 多 feature_set 训练数据构造

### 场景

训练模型时，通常需要把多个特征集（例如价格动量特征 + 成交量特征 + 波动率特征）合并成一张宽表，每行对应一个时间点。

### 三种 join_mode

| 模式 | 适用场景 | 特点 |
|------|---------|------|
| `concat`（默认） | 单特征集，或调用方自行 merge | 垂直拼接，向后兼容 |
| `exact` | 所有特征集在完全相同的时间戳上计算 | inner join；列名自动加 `{feature_set_id}__` 前缀避免冲突 |
| `asof` | 特征集采样频率不同，如日频特征 join 分钟频特征 | 时序左 join；`direction="backward"` 保证不泄露未来 |

### exact join 示例

```python
spec = FeatureDatasetSpec(
    feature_store_path="outputs/features",
    feature_set_ids=["vwm_features_v1", "vol_features_v1"],
    instruments=["BTCUSDT-PERP.BINANCE"],
    join_mode="exact",
    column_prefix=True,         # 默认 True，避免列名冲突
    select_columns={
        "vwm_features_v1": ["momentum", "vwm"],
        "vol_features_v1": ["iv", "rv"],
    },
)
df = load_feature_dataset(spec)
# 输出列：ts_event, instrument_id, vwm_features_v1__momentum,
#          vwm_features_v1__vwm, vol_features_v1__iv, vol_features_v1__rv
```

### asof join 示例

```python
spec = FeatureDatasetSpec(
    feature_store_path="outputs/features",
    feature_set_ids=["bar_features_v1", "daily_features_v1"],
    join_mode="asof",    # 每分钟 bar join 最近一条日频特征
    column_prefix=True,
)
df = load_feature_dataset(spec)
```

### 带 metadata 的加载

```python
from nautilus_ext.ml.feature_dataset import load_feature_dataset_with_metadata

result = load_feature_dataset_with_metadata(spec)
print(result.row_count)          # 8760
print(result.used_feature_sets)  # ['vwm_features_v1', 'vol_features_v1']
print(result.start, result.end)  # 时间范围
```

---

## 16. Exact join vs Asof join

### Exact join（等值连接）

- 使用 `pandas.DataFrame.merge(on=join_keys, how="inner")`
- 默认 `join_keys=["instrument_id", "ts_event"]`
- 仅保留所有特征集都有数据的时间点
- 适合：同一 pipeline 内不同 engine 在同一 bar 上计算的特征

```
bar ts=100:  vwm_features [x=1.0, y=2.0] + vol_features [z=0.5]
             → exact join → 一行: ts=100, vwm__x=1.0, vwm__y=2.0, vol__z=0.5
```

### Asof join（时序近似连接）

- 使用 `pandas.merge_asof(direction="backward")`
- 按 instrument_id 分组后逐组 join，避免跨品种污染
- 对每个 primary 行，找 `ts_event <= primary_ts` 的最近一条 secondary 行
- 若没有满足条件的 secondary 行，对应列为 NaN
- 适合：日频宏观特征 join 分钟频行情特征

```
primary  ts=60:  vwm_features [x=1.0]
secondary        日频特征最近一条 ts=30 → 取 ts=30 的值（不用 ts=90）
```

**Point-in-time 保证**：asof join 不会把 `ts > primary_ts` 的特征引入训练，避免前瞻偏差。

---

## 17. Point-in-time Correctness 如何保证

| 保障机制 | 位置 | 说明 |
|---------|------|------|
| `is_warmup` 标记 | `FeaturePipeline.warmup()` | warmup 期间产出的特征打标为 `is_warmup=True` |
| 默认排除 warmup | `OfflineFeatureStore.query(include_warmup=False)` | 训练数据不包含 warmup 行 |
| asof direction=backward | `_asof_join()` | 仅使用 `ts ≤ primary_ts` 的特征 |
| `feature_version` 字段 | `ManifestRecord`, `FeatureEvent` | 特征逻辑变化时升版本，旧数据可独立查询 |
| 离线 flush 批量 | `OfflineFeatureStore.flush()` | 写入 Parquet 时携带完整 timestamp，不丢失时序信息 |

**注意**：exact join 使用 inner join，若某个特征集在某时间点无数据，该行不进入训练集，避免隐式 NaN 填充带来的偏差。

---

## 18. Manifest 维护

随着数据积累，manifest 可能产生冗余记录（重复 flush、文件移动等）。提供四个维护方法：

```python
from nautilus_ext.features.feature_manifest import FeatureManifest

m = FeatureManifest("outputs/features/feature_manifest.json")
m.load()

# 去重：移除完全相同的 6 字段重复记录
n_removed = m.deduplicate()

# 压缩：每个 (fs, ver, iid, start, end) 时间槽只保留最新记录
n_compacted = m.compact(keep="latest")   # or keep="first"

# 清理失效文件：移除 Parquet 已被删除的 manifest 记录
missing_paths = m.remove_missing_files()

# 统计摘要
stats = m.summary()
# 返回：{feature_set_id: {instrument_id: {file_count, total_row_count, min_start_ts, max_end_ts}}}
for fsid, iid_stats in stats.items():
    for iid, info in iid_stats.items():
        print(f"{fsid}/{iid}: {info['file_count']} files, {info['total_row_count']} rows")

# 保存：维护完毕后手动保存
m.save()
```

**何时运行维护**：
- 回测结束后运行一次 `deduplicate()` + `compact()` 即可
- `remove_missing_files()` 适合在清理旧数据目录后运行
- 维护操作不会修改 Parquet 文件本身，只更新 JSON index

---

## 19. Benchmark 结果如何解释

运行方式：

```bash
# 从项目根目录（需要 nautilus_ext 在 PYTHONPATH）
PYTHONPATH=. python scripts/benchmark_feature_store.py

# 调整参数
PYTHONPATH=. python scripts/benchmark_feature_store.py --n 100000 --events 10000 --files 100
```

典型结果（M2 MacBook Air）：

| 指标 | 典型值 | 含义 |
|------|--------|------|
| `get_latest`     | 0.04–0.15 µs | O(1) dict 查找，热路径核心调用 |
| `get_all_latest` | 0.06–0.20 µs | O(n_fsids) dict 浅拷贝 |
| `get_window(50)` | 0.5–2.0 µs   | deque → list 转换 |
| append+flush     | ~50 ms / 1k events | 含 DataFrame 构造和 parquet 写盘 |
| manifest 查询    | 0.01–0.05 ms | JSON 内存过滤，与文件数无关 |
| rglob 查询       | 0.1–10 ms    | 随文件数线性增长 |
| 加速比           | 10–200×      | 文件数越多，加速比越高 |

**热路径预算参考**：
- 1 分钟 bar（1 次/分钟）：get_latest < 1 µs，预算充裕
- tick 数据（1000 次/秒）：get_latest < 0.2 µs，仍在安全范围

---

## 20. 当前 JSON Manifest 的边界

| 限制 | 说明 |
|------|------|
| 文件大小 | 每条记录约 200 字节；10 万条 ≈ 20 MB JSON — 可接受 |
| 加载速度 | 10 万条 JSON 解析约 200 ms — 仅在 OfflineFeatureStore 初始化时加载一次 |
| 并发写入 | 不支持多进程并发 append_file_record；需单写多读 |
| 原子性 | save() 直接覆写；异常中断可能导致截断 — 建议先写临时文件再 rename |

**适用规模**：< 5 万个 Parquet 文件（约 5 年 × 1 分钟 bar × 10 品种）。超出此规模建议升级到 DuckDB 或 Parquet 格式 manifest。

---

## 21. 后续升级 DuckDB / Parquet Manifest 建议

当 feature store 超过 5 万文件或需要跨机器共享 manifest 时，建议按以下步骤升级：

1. **接口不变**：`FeatureManifest.find_files()` / `append_file_record()` / `save()` 签名不变，调用方无感知
2. **后端替换**：将 `self._records: list[ManifestRecord]` 替换为 DuckDB in-memory 表或 Parquet 文件
3. **迁移脚本**：读取旧 JSON → 写入新格式；`all_records()` 接口已返回 `list[ManifestRecord]`，可直接复用

```python
# 迁移示例（不需要修改调用方代码）
old_manifest = FeatureManifest("outputs/features/feature_manifest.json")
old_manifest.load()

# 写入 Parquet manifest（需要新实现的 ParquetFeatureManifest）
new_manifest = ParquetFeatureManifest("outputs/features/manifest.parquet")
for record in old_manifest.all_records():
    new_manifest.append_file_record(record)
new_manifest.save()
```

DuckDB 方案优点：
- SQL 查询灵活，支持聚合统计
- 列式存储，10 万条记录文件 < 1 MB
- `find_files()` 可用 SQL WHERE 过滤，无需全量加载到内存

---

## 22. 端到端 Demo 与低代码接入流程

### 22.1 Demo 数据流

`examples/nautilus_ext_feature_database/run_feature_database_demo.py` 展示了完整的 Feature Database 生命周期，不依赖真实网络：

```
mock BarInput × 120
       │
       ▼
 FeaturePipeline
       │
       ├── warmup(bars[:20])     → is_warmup=True，只更新 OnlineFeatureStore
       │
       ├── update(bar) × 100    → 实时路径（热路径，无 DataFrame）
       │        │
       │        ├──▶ OnlineFeatureStore  O(1) 内存索引（实时推理）
       │        └──▶ OfflineFeatureStore 缓冲队列（等待 flush）
       │
       └── flush()               → 批量写 Parquet + 更新 FeatureManifest
                │
                ├──▶ offline/demo_mom_v1/BTCUSDT-PERP_BINANCE/<ts>.parquet
                └──▶ feature_manifest.json
```

运行方式：

```bash
PYTHONPATH=. python examples/nautilus_ext_feature_database/run_feature_database_demo.py
```

示例输出：

```
============================================================
Feature Database Demo — Summary
============================================================
  generated_events      : 100
  rows_flushed_parquet  : 100
  manifest_records      : 1
  offline_files         : ['outputs/examples/feature_database_demo/offline/demo_mom_v1/...']
  dataset_shape         : (81, 9)
  dataset_columns       : ['ts_event', 'ts_init', 'instrument_id', ..., 'close_ma', 'momentum', ...]
  inference_vector_keys : ['demo_mom_v1.close_ma', 'demo_mom_v1.momentum', ...]
```

---

### 22.2 新特征如何按模板开发

复制 `nautilus_ext/features/templates/example_feature_engine.py`，按以下步骤修改：

**第一步：确定 FEATURE_SET_ID**
```python
FEATURE_SET_ID = "my_new_feature_v1"   # 确定后不能改名，否则历史数据找不到
```

**第二步：填写 FeatureSetSpec（schema 先行）**
```python
MY_FEATURE_SCHEMA = FeatureSetSpec(
    feature_set_id=FEATURE_SET_ID,
    version="1",
    input_types=["bar"],
    output_features=[
        FeatureFieldSpec("my_signal", "float", nullable=True, description="..."),
        FeatureFieldSpec("bar_count", "int", nullable=False, description="..."),
    ],
    required_history=20,
    point_in_time_safe=True,
)
```

**第三步：实现 update()（热路径，禁止创建 DataFrame）**
```python
def update(self, event) -> FeatureEvent | None:
    if not isinstance(event, BarInput):
        return None        # 忽略不支持的事件类型
    # ... 计算特征（纯 Python，不创建 DataFrame）
    return FeatureEvent(
        ts_event=event.ts_event,
        instrument_id=event.instrument_id,
        feature_set_id=FEATURE_SET_ID,
        feature_version="1",
        values={"my_signal": ..., "bar_count": ...},
        source_event_type="bar",
    )
    # is_warmup 由 FeaturePipeline 打标，engine 不设置
```

**第四步：实现 state_dict / load_state_dict（支持热重启）**
```python
def state_dict(self) -> dict:
    return {"window": self._window, "closes": list(self._closes), ...}

def load_state_dict(self, state: dict) -> None:
    self._window = state["window"]
    self._closes = deque(state["closes"], maxlen=self._window)
```

**第五步：注册**
```python
from nautilus_ext.features.feature_registry import register_feature_engine

@register_feature_engine(FEATURE_SET_ID)
class MyNewFeatureEngine(FeatureEngineBase):
    ...
```

注册后，所有地方都可以通过 ID 构建：
```python
from nautilus_ext.features.feature_registry import build_feature_engine
engine = build_feature_engine("my_new_feature_v1", params={"window": 14})
```

---

### 22.3 新策略如何按模板开发

复制 `nautilus_ext/strategies/templates/example_signal_engine.py`，按以下步骤修改：

**第一步：声明依赖的 feature_set**
```python
SIGNAL_NAME = "my_strategy_v1"
REQUIRES_FEATURES = ["my_new_feature_v1", "vwm_features_v1"]
```

**第二步：从 StrategyRuntimeContext 读取特征（不要重复计算）**
```python
def update(self, event, context: StrategyRuntimeContext | None = None) -> SignalResult:
    if context is None:
        return SignalResult(signal_name=SIGNAL_NAME, reason="no_context")

    # 读取预计算特征 — 绝不在策略里重新计算已有的特征
    my_signal = context.get_value("my_new_feature_v1", "my_signal")
    vwm = context.get_value("vwm_features_v1", "vwm")

    # 必须做 None 检查 — 引擎未 warmup 完时特征为 None
    if my_signal is None or vwm is None:
        return SignalResult(signal_name=SIGNAL_NAME, reason="features_not_ready")

    # ... 决策逻辑
```

**第三步：返回 SignalResult**
```python
return SignalResult(
    signal_name=SIGNAL_NAME,
    order_intents=[
        OrderIntent(instrument_id=..., action="submit", side="buy", order_type="market"),
    ],
    reason="entry_long",
    debug={"my_signal": my_signal, "vwm": vwm},
)
```

---

### 22.4 主配置如何切换特征组合

在 strategy_spec JSON 中声明 `requires_features` 和 `feature_specs`：

```json
{
  "strategy": {
    "name": "my_strategy_v1",
    "requires_features": ["my_new_feature_v1", "vwm_features_v1"],
    "feature_specs": {
      "my_new_feature_v1": { "window": 14 },
      "vwm_features_v1":   { "mom_len": 5, "avg_len": 20, "atr_len": 5 }
    },
    "params": { ... }
  },
  "feature_database": {
    "enabled": true,
    "store_path": "outputs/features/my_strategy_btc_1m",
    "online_window_size": 500,
    "offline_flush_threshold": 1000
  },
  "execution": {
    "dry_run": true,
    "enable_order_submit": false
  }
}
```

完整样例见：`examples/strategy_specs/vwm_with_feature_database.json`

Runner 读取 `requires_features` → 自动构建 FeaturePipeline → 注入 `StrategyRuntimeContext`。  
切换特征组合只需修改 JSON，无需改动策略代码。

---

### 22.5 signals.csv 与 Feature Database 的区别

| 维度 | signals.csv | Feature Database |
|------|------------|-----------------|
| 特征存储位置 | debug 字段（混在信号行里） | 独立 Parquet 文件（按 feature_set_id + instrument_id 分目录） |
| Schema 管理 | 无，列名随意变化 | FeatureSetSpec + feature_version，变更必须 bump 版本 |
| warmup 区分 | 无，需手动过滤行号 | is_warmup 字段，训练默认 include_warmup=False |
| 多策略共享 | 每个策略单独计算 | 同一 FeaturePipeline 输出，多策略读同一个 OnlineFeatureStore |
| 实时推理路径 | 无 | OnlineFeatureStore O(1) 查找，无文件 I/O |
| 训练读取 | 手工 pandas 过滤 | FeatureDataset（支持 exact/asof join、时间范围、列筛选） |
| point-in-time 保证 | 依赖调用方自律 | is_warmup 标记 + asof join direction="backward" 强制执行 |
| 索引效率 | 每次全量 rglob | FeatureManifest JSON 索引，按时间范围过滤，127× 加速 |
| 热重启支持 | 需重新下载数据重跑 | state_dict / load_state_dict + warmup 快速恢复 |

**结论**：signals.csv 适合快速 debug 单策略输出；Feature Database 是生产级的统一数据层，适合多策略、多特征、长周期运营场景。
