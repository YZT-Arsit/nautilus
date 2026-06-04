# Feature Database 端到端 Demo

本 demo 展示 `nautilus_ext` Feature Database 的完整数据流，用于向团队证明：

- 历史数据、实时数据、特征可以作为统一数据层
- 特征可以在线读取、离线落盘、训练读取、推理读取
- 新策略/新特征可以低代码接入

**无需真实网络连接**，所有数据均通过纯 Python 模拟生成。

---

## 如何运行

```bash
# 从仓库根目录运行
PYTHONPATH=. python examples/nautilus_ext_feature_database/run_feature_database_demo.py

# 指定自定义输出目录
PYTHONPATH=. python examples/nautilus_ext_feature_database/run_feature_database_demo.py \
    --output-dir /tmp/my_feature_demo
```

默认输出目录：`outputs/examples/feature_database_demo/`

---

## 输出文件

```
outputs/examples/feature_database_demo/
├── feature_manifest.json          # 文件级索引：(fsid, iid, ts range) → Parquet 路径
└── offline/
    └── demo_mom_v1/
        └── BTCUSDT-PERP_BINANCE/
            └── <start_ts>-<end_ts>.parquet   # 特征数据（含列：ts_event, close_ma, momentum, ...）
```

---

## 数据流说明

```
mock BarInput × 120
       │
       ▼
  pipeline.warmup(bars[:20])     ← is_warmup=True，仅更新 OnlineFeatureStore
       │
  pipeline.update(bar)           ← 逐条实时更新（热路径，无 DataFrame）
       │
       ├──▶ OnlineFeatureStore   ← O(1) 内存读取，供推理路径使用
       │       get_latest(iid, fsid)
       │
       └──▶ OfflineFeatureStore  ← 缓冲 → flush() → Parquet 文件
                │
                └──▶ FeatureManifest.json  ← 记录文件索引，避免 rglob
```

---

## 四条读取路径

### OnlineFeatureStore（实时推理路径）

```python
fe = online_store.get_latest("BTCUSDT-PERP.BINANCE", "demo_mom_v1")
print(fe.values)   # {"close_ma": 42100.5, "momentum": 80.3, ...}
```

- **O(1) dict 查找**，无文件 I/O
- 供 InferenceContext 和信号引擎使用
- 数据只在内存中，进程重启后丢失 → 需要 warmup 重建

### OfflineFeatureStore（持久化路径）

```python
offline_store.append(feature_event)   # 缓冲
offline_store.flush()                 # 批量写 Parquet
```

- 避免每条事件一个小文件
- 批量写入提升吞吐（约 67k events/s）
- 落盘后通过 FeatureManifest 索引，query() 不再需要 rglob

### FeatureDataset（训练读取路径）

```python
spec = FeatureDatasetSpec(
    feature_store_path="outputs/features",
    feature_set_ids=["demo_mom_v1"],
    instruments=["BTCUSDT-PERP.BINANCE"],
    include_warmup=False,   # 排除 is_warmup=True 行，保证 point-in-time 正确性
)
df = load_feature_dataset(spec)   # 返回 pandas DataFrame，可直接用于训练
```

- `include_warmup=False` 防止 look-ahead bias
- 支持多 feature_set join（concat / exact / asof）
- 读取 Parquet，不依赖 OnlineFeatureStore

### InferenceContext（推理路径）

```python
ctx = ModelInferenceContext(
    online_store=online_store,
    feature_set_ids=["demo_mom_v1"],
    feature_order=["demo_mom_v1.close_ma", "demo_mom_v1.momentum"],
    missing_feature_policy="fill_none",
)
vector = ctx.get_feature_vector("BTCUSDT-PERP.BINANCE")
# → {"demo_mom_v1.close_ma": 42100.5, "demo_mom_v1.momentum": 80.3}
arr = ctx.get_feature_array("BTCUSDT-PERP.BINANCE")
# → numpy.ndarray([42100.5, 80.3])
```

- 读 OnlineFeatureStore，无 Parquet I/O
- 支持 fill_none / fill_zero / raise 策略
- get_feature_array() 直接输出 numpy array，可送入 sklearn/XGBoost 模型

---

## 为什么不依赖 signals.csv

| signals.csv 方式 | Feature Database 方式 |
|---|---|
| 列名无版本控制，悄悄变化 | feature_version 字段 + FeatureSetSpec schema |
| 每次读全量文件 | manifest 索引，只读时间范围内的文件 |
| 难以区分 warmup 数据 | is_warmup 字段，训练默认排除 |
| 无法多 feature_set join | concat / exact / asof join 内置 |
| 无实时路径 | OnlineFeatureStore O(1) 实时读取 |
| 特征和信号混合 | feature layer 与 signal layer 解耦 |

---

## 新特征开发模板

参见：`nautilus_ext/features/templates/example_feature_engine.py`

```python
# 三步接入新特征
from nautilus_ext.features.feature_registry import build_feature_engine
engine = build_feature_engine("example_obv_v1")
pipeline = FeaturePipeline(feature_engines=[engine], online_store=..., offline_store=...)
```

## 新策略开发模板

参见：`nautilus_ext/strategies/templates/example_signal_engine.py`

策略通过 `StrategyRuntimeContext` 读取预计算特征，无需重复计算：

```python
obv = context.get_value("example_obv_v1", "obv")   # None if not ready
roc = context.get_value("example_obv_v1", "roc")
```

---

## 相关文档

- 完整设计文档：`docs/nautilus_ext_feature_database_design_zh.md`
- benchmark 脚本：`scripts/benchmark_feature_store.py`
- 策略配置样例：`examples/strategy_specs/vwm_with_feature_database.json`
