# 量化平台架构与锁定接口 (Platform Architecture & Locked Interfaces)

本文件描述整理后的量化平台架构与**永久锁定的公开接口**。data / feature /
results 三层为完全自研；回测/实盘执行接入 Nautilus。接口一经锁定，后续新增
策略/特征只在既定接口上扩展，不改接口本身。

> upstream 目录 `nautilus_trader/`、`crates/`、`python/` 是 Nautilus 库本体，
> **本平台不修改它们**，仅作为执行引擎依赖使用。

---

## 1. 分层总览

```
run_strategy.py                用户入口：跑单个策略（回测/实盘）
run_batch.py                   用户入口：批量回测（多标的 × 多参数 × 手续费开关）
        │
        ▼
data_engine/        自研·数据层    历史数据 / 实盘数据 / 特征数据 统一为「数据」
feature_engine/     自研·特征层    算子库(固定文件夹) + 特征计算/保存/复用
strategy_framework/ 策略骨架       StrategyPlugin + registry + Nautilus 执行后端
strategies/         策略实现       每策略一个文件夹，只写 build_specs + 信号逻辑
results/            自研·结果层    报表 / run_uid / PnL / 图表 / 本地查看器
```

**核心理念**：历史数据、实盘数据、特征计算结果都是「数据」，级别相同，
统一存储于 `historical_data/`（parquet，服务器端）。特征算子是可复用小算子，
放在固定文件夹 `feature_engine/compute/feature_lib/`；策略只声明要用哪些算子
（`build_specs`）并写开单信号逻辑（`on_snapshot`），不自己算指标。

---

## 2. 数据层接口 (`data_engine`) — LOCKED

### 2.1 事件类型（唯一）

`data_engine/events.py`（纯 Python，无第三方依赖）：

- `BarEvent(open, high, low, close, volume, instrument_id, event_time_ns, event_type="bar")`
- `TradeEvent(event_time_ns, instrument_id, price, quantity, quote_quantity, side, ...)`
- `QuoteEvent(event_time_ns, instrument_id, bid_price, ask_price, bid_size, ask_size, ...)`

所有时间戳统一为 **纳秒 int64** (`event_time_ns`)。全平台只此一套事件类型
（旧 `nautilus_ext.data.events` 重复类型已删除）。

### 2.2 数据加载入口

```python
from data_engine import load_events
warmup_events, live_events = load_events(data_config: dict) -> tuple[list, Iterable]
```

`data_config["mode"]` 选择数据源（注册于 `data_engine/loader.py::_LOADERS`）：

| mode | 产出 | 说明 |
|------|------|------|
| `synthetic` / `synthetic_trades` | Bar/Trade | 确定性合成数据（demo/测试） |
| `csv_bars` | Bar | 单 CSV 文件 |
| `parquet_bars` (`hive_parquet_bars`) | Bar | Hive 分区 parquet（回测主用） |
| `parquet_trades` (`hive_parquet_trades`) | Trade | Hive 分区 parquet |
| `live_synthetic` / `live_gateway` | Bar | 实盘槽位（本期为 stub，架构预留） |

**新增数据源**：在 `data_engine/sources/` 加一个 `load_xxx(config)->(warmup, live)`
文件 + 在 `loader.py::_LOADERS` 注册一行。

### 2.3 统一 parquet 存储布局 (LOCKED — 永久)

服务器端根目录 `historical_data/`，三类数据资产 + manifests 同根平级：

```
historical_data/
  market_data/                          # 行情数据（历史+实盘落盘）
    asset_class=<crypto|future|stock>/
      exchange=<BINANCE|CFFEX|...>/
        symbol=<BTCUSDT|IH2303|...>/
          data_type=<bar|trade|quote>/
            freq=<1m|5m|1h|tick>/
              date=<YYYY-MM-DD>/
                part-*.parquet
  feature_data/                         # 特征数据（与 market_data 平级，同属「数据」）
    feature_set=<technical|order_flow|...>/
      asset_class=.../ exchange=.../ symbol=.../
        freq=<...>/ date=<YYYY-MM-DD>/
          part-*.parquet
  instruments/                          # 合约元数据快照
    exchange=<...>/ asof=<YYYY-MM-DD>/ part-*.parquet
  manifests/                            # 可追溯 + 防重算
    dataset_manifest/ ...               # 行情落盘记录
    feature_manifest/  ...              # 特征计算记录 (feature_set, params_hash, version)
```

设计要点（对齐真实量化公司标准）：
- **`data_type` 与 `symbol` 是一等分区维度**：bar/trade/quote 分开，单标的可精准裁剪。
- **feature_data 结构镜像 market_data**：特征即数据，读写方式一致。
- **列规范**：行情 `event_time_ns:int64, open/high/low/close/volume:float64, symbol:str`；
  特征 `event_time_ns:int64, symbol:str, <feature_name>:float64, ...`。
- **分区裁剪 + 列裁剪**：pyarrow.dataset + Hive，谓词下推。
- **manifest** 记录 `(partition_key, name, version, params_hash, rows, computed_at, source)`，
  防止重复计算，支持增量与可追溯。

路径构造/解析集中在 `feature_engine/storage/layout.py`（读写共用，保证一致）。

---

## 3. 特征层接口 (`feature_engine`) — LOCKED

### 3.1 唯一算子库（固定文件夹）

`feature_engine/compute/feature_lib/` 是**唯一**的特征算子库。每个算子是一个
纯 Python 增量类（`update(event)->value`，持有滚动状态），按域分文件：

```
feature_lib/
  price_action.py   rolling_range, price_position, breakout_*, candle_*, shadow_*
  returns.py        return_n, momentum_n
  volatility.py     true_range, atr, volatility_ratio, bollinger_*
  volume.py         volume_ratio, volume_zscore, quote_volume, vwap_distance
  trade.py          trade_count/volume/imbalance/vwap/intensity ...（tick 级）
  normalization.py  zscore
  ema.py            ema, vwm（VWM 复合算子，见 §5）
```

同一套算子既用于**流式/回测**（`SpecFeatureEngine` 逐事件 `update`），也用于
**离线特征预计算**（回放同一算子写入 `feature_data`），保证 回测≡离线 一致。

**新增算子**：在对应 `feature_lib/*.py` 写一个算子类 + 在 `feature_lib/__init__.py`
注册到 `PythonBackend`（`params["type"] -> 类`），并在 `builders.py` 加一个
`xxx_spec()` 便捷构造器。

### 3.2 声明式规格 + 快照（策略侧公开面）

策略只从 `feature_engine.api` 导入：

```python
from feature_engine.api import FeatureSpec, FeatureSnapshot, rolling_mean_spec, atr_spec, ...
```

- `FeatureSpec(name, input_type, input_field, window, window_unit, trigger, params, depends_on)`
  — 声明一个特征。`depends_on` 支持特征依赖特征（派生特征，拓扑顺序更新）。
- `FeatureSnapshot` — 某标的某时刻的全部特征值。策略读取：
  `snapshot.value(name, default)` / `snapshot.is_ready(name)` / `snapshot.ts_event` /
  `snapshot.instrument_id`。
- `builders.py` 提供 `xxx_spec()` 便捷构造器（隐藏 `params["type"]`）。

### 3.3 运行驱动

```python
from feature_engine.runner import FeatureStrategyRunner
runner = FeatureStrategyRunner(specs, strategy)   # strategy 只需 on_snapshot(snapshot)->signal
runner.warmup(warmup_events)
for event, snapshot, signal in runner.run(live_events): ...
```

### 3.4 离线特征计算与复用

```python
from feature_engine.offline import HistoricalFeatureBuilder
builder = HistoricalFeatureBuilder(specs)                 # 复用同一批 FeatureSpec
df = builder.build_from_market_store(root, symbol, freq, date)   # 读 market_data → 算特征
builder.write_feature_data(df, feature_set, ...)                 # 写 feature_data + manifest
```

以及回读：`feature_engine.storage.FeatureDataReader.scan_features(...)`。
「策略把算好的特征当数据复用再计算再更新」通过：读 `feature_data` → 作为
`FeatureVectorInput`/派生特征输入 → 再算 → 写回新 `feature_set`。

---

## 4. 策略层接口 (`strategy_framework` + `strategies`) — LOCKED

### 4.1 插件契约

`strategy_framework/plugin.py`：

```python
@dataclass(frozen=True)
class StrategyPlugin:
    name: str
    config_cls: type                              # 参数 dataclass
    strategy_cls: type                            # 实现 on_snapshot(snapshot)->"BUY"|"SELL"|"HOLD"
    build_specs: Callable[[object], list[FeatureSpec]]
    default_config_path: str | None = None
```

策略实现只需：`build_specs(config)`（声明用哪些算子）+ `Strategy.on_snapshot()`
（开单信号逻辑，返回 BUY/SELL/HOLD）。**策略不下单、不接触组合、不 import
nautilus**（VWM 用到的 EMA/ATR 走特征算子）。

### 4.2 新增策略（单文件夹 + 一行注册）

1. 建 `strategies/<name>/`：`strategy.py`(定义 `PLUGIN`) + `__init__.py`(导出 PLUGIN)
   + `config.yaml`。
2. 在 `strategy_framework/registry.py` 加一行：
   `from strategies.<name> import PLUGIN as X` 并加入 `STRATEGY_REGISTRY`。

### 4.3 执行后端与手续费

`strategy_framework/backends/`（工厂 `build_backend(execution_cfg, spec_names, ctx)`）：

- `nautilus_backtest`（主）：`mode: simulated`（无依赖参考撮合）或 `nautilus_native`
  （真实 `nautilus_trader.backtest.BacktestEngine`）。
- `paper` / `signal_recorder`：轻量记录。
- `nautilus_live`：实盘 stub（本期不启用，架构预留）。

**手续费一等公民**：`execution.fee_scenarios: [0.0, 0.0005]` — 一次运行对每个费率
各跑一遍，结果分目录（`.../fee_0/`, `.../fee_5bps/`），结果表并排对比。
单费率可用 `execution.fee_rate`（等价 `fee_scenarios: [fee_rate]`）。

---

## 5. VWM 特例说明

VWM = `XAverage(Vol * Momentum(Close, MomLen), AvgLen)`，`AATR = AvgTrueRange(ATRLen)`。
其 EMA/ATR 用 Nautilus 原生指标（`ExponentialMovingAverage` / `AverageTrueRange`），
封装为 `feature_lib/ema.py` 的算子；VWM 复合逻辑与信号（保留 TradeBlazer `[1]`
语义：入场用上一根 `SEPrice`/`ATR`，setup 窗口用上一根 `SSetup`，出场用上一根
`BullSetup`）全部自包含在 `strategies/vwm_short/`，不依赖已删除的 `nautilus_ext`。

---

## 6. 结果层接口 (`results`) — LOCKED

- `results/report.py`：`write_backtest_report(...)` 产出 `signals/intents/fills/
  trades/positions/equity_curve.csv` + `metrics.json` + `report.md`（与撮合来源无关，统一格式）。
- `results/run_uid.py`：确定性 `run_uid`（策略/标的/窗口/参数hash/费率 → 稳定 ID），
  作为结果复用锚点。
- `results/charts.py`：读 `equity_curve.csv` → PnL 序列 + matplotlib 图（equity/
  drawdown/pnl/position），matplotlib 缺失时降级只出 CSV。
- `results/viewer`：本地只读查看器（无需重跑、无需网络），看图表/表格。

输出目录：`outputs/backtests/<run_uid>/`（单跑）、`outputs/batches/<batch>/`（批量聚合）。
服务器算完把 `outputs/` 里的结果（表+图）拉回**本地**查看；原始数据只留服务器。

---

## 7. 配置 schema (LOCKED)

```yaml
run_name: <str>                 # 可选，缺省用 run_uid
strategy: <registered-name>
params: { <strategy config fields> }
data:
  mode: <synthetic|csv_bars|parquet_bars|parquet_trades|...>
  root: historical_data/market_data      # parquet 模式
  asset_class: crypto
  exchange: BINANCE
  symbol: BTCUSDT
  data_type: bar
  freq: 1m
  start: 2026-03-01
  end:   2026-05-31
  warmup_bars: 200
execution:
  backend: nautilus_backtest
  mode: nautilus_native                   # 或 simulated
  initial_cash: 100000
  quantity: 1.0
  sell_means: short                       # short | flat
  allow_short: true
  fee_scenarios: [0.0, 0.0005]            # 无费 vs 有费，成对回测
  fill_timing: next_bar                   # same_bar | next_bar
output:
  root: outputs/backtests
```

---

## 8. 服务器同步与校验

- 同步：`scripts/sync.sh`，**scp** 推送代码（仅工作于 `D:\nautilus`）；数据只留服务器。
- 校验：远端 `uv run pytest ...` / `uv run python run_strategy.py ...`（本地无 polars，
  测试在服务器跑）。结果 `outputs/` 拉回本地查看。
- 服务器：`172.16.112.43`，用户 `quant_data`，路径 `D:\nautilus`。

---

## 9. 「新增」速查

| 目标 | 改动 |
|------|------|
| 新增特征算子 | `feature_lib/<domain>.py` 加类 + `__init__.py` 注册 + `builders.py` 加 `xxx_spec()` |
| 新增策略 | `strategies/<name>/` 三文件 + `registry.py` 一行 |
| 新增数据源 | `data_engine/sources/load_xxx.py` + `loader.py` 一行 |
| 新增回测场景 | `configs/backtests/<name>.yaml` |
| 批量/多标的 | 批量 config（symbols 列表 × params × fee_scenarios）→ `run_batch.py` |
```
