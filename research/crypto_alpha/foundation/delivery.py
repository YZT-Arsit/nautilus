"""Generate the benchmark contract documents and evidence index."""
import pandas as pd


def deliver(out, cfg, models, portfolios, tests, parity):
    interfaces = [
        ("Market", "foundation/data.py::MarketSource.read", "data/market.py::read_bars/read_funding; data_engine/sources/{hive_partitioning,parquet_funding}.py", "1m StandardBar and official settlement events", "MarketSlice"),
        ("Factor", "foundation/registry.py::Registry.register", "factors/library.py::compute/add_cross_section; v2/features.py::local_features/cross", "Completed minute inputs or a same-time panel", "Indexed feature series and formula registry"),
        ("Label", "foundation/dataset.py::task", "labels/alignment.py::align/normalize", "Prediction clock and exact delayed close prices", "Raw, rank, Rank Z-score and clocks"),
        ("Dataset", "foundation/dataset.py::CrossSectionDataset.view", "training/protocol.py::Panel", "Deterministic Cartesian asset grid plus known-time mask", "Features, masks and mature targets"),
        ("Strength", "foundation/evaluation.py::relative_strength", "portfolio/weights.py::weights for the execution policy", "Per-timestamp finite scores", "Raw/rank/demean/zscore/rank_zscore/l1"),
        ("Evaluation", "foundation/evaluation.py::evaluate/factor_report", "training/metrics.py::section_ic/score_summary; training/protocol.py::factor_diagnostics", "Cross-sectional data and targets", "IC, Rank IC, ICIR, monthly, decay, correlations, terciles and turnover"),
        ("Model", "foundation/model.py::Model", "models/cpu.py::Preprocessor/fit_model/prediction/ic_loss", "Same dataset and supervision for three families", "Persisted scaler, estimator, scores and evaluation"),
        ("Annual", "foundation/annual.py::train_family", "training/protocol.py::annual_windows", "Half-open calendar intervals and publication cutoff", "Fit, validation, refit and frozen OOS year"),
        ("Portfolio", "foundation/portfolio.py::replay", "backtest/report.py::portfolio; portfolio/interface.py::validate_export", "Delayed score export and chosen weight method", "Neutral targets and accounting evidence"),
        ("Backtest", "backtest/replay.py::replay", "strategy_framework/backends/nautilus_simulation.py::IntentFillSimulator; strategy_framework/execution/backtest_report.py::_Accountant", "Bar open price proxies, simulated target deltas and funding", "Execution ledger, funding and costs"),
    ]
    pd.DataFrame(interfaces,columns=["module","interface","existing_source","input","output"]).to_csv(out/"interface_registry.csv",index=False)
    (out/"architecture.md").write_text("""# 基座架构

```text
Nautilus Hive StandardBar + FundingEvent
  → MarketSource.read → Registry.symbol / Registry.section
  → exact-minute clocks → CrossSectionDataset.view
  → validation → annual refit → frozen predictions
  → per-clock IC + score transforms → target positions
  → Nautilus IntentFillSimulator + _Accountant → ledger
```

源目录只读，原始分钟行情逐币读入内存。仅保存抽样后的特征、标签和预测面板以及 Funding 事件。回放只调用本地成交模拟器。账本按持仓变化计算费用，并对期末平仓、Funding 未结算笔数及权益恒等式验收。

`configs/foundation_v1.json` 指定基座策略。V2 的 EMA、部分调仓与换手约束位于 `v2/execution.py` 和 `v2/portfolios.py`。GRU、StockMixer 和 MASTER 位于 `v2/networks.py`，与三个基座模型独立。

各层输入、输出及复用位置见 `interface_registry.csv`。
""",encoding="utf-8")
    (out/"dataset_schema.md").write_text("""# 数据协议与时钟

| 字段 | 来源及单位 | 可用时间 |
|---|---|---|
| symbol | Nautilus Hive Binance USDⓈ-M 线性永续合约标识 | 同分区 |
| timestamp | StandardBar 的 UTC 开盘戳加 1 分钟，表示完整 Bar 的结束时间 | 完整 Bar 结束时 |
| open/high/low/close | StandardBar，USDT／交易标的基础单位 | 因子使用完整 Bar 结束时 |
| volume | StandardBar 基础单位成交量。1000PEPEUSDT 为合约基础单位 | 完整 Bar 结束时 |
| quote_volume | 币安报价成交额，USDT | 完整 Bar 结束时 |
| trade_count | 币安成交笔数 | 完整 Bar 结束时 |
| taker_buy_volume | 币安主动买基础成交量 | 完整 Bar 结束时 |
| taker_buy_quote_volume | 币安主动买报价成交额，USDT | 完整 Bar 结束时 |
| funding | Nautilus FundingRateEvent，结算比例，正值为多头付费 | 官方结算时间，分钟协议向上取整，因子只读严格早于预测的结算 |
| eligible | 预测时已完成且有效的交易 Bar | prediction_time |
| feature_available_time | 特征输入最后允许可用的 UTC 时点 | 不晚于 prediction_time |
| prediction_time | 横截面批次 UTC 时钟 | 该时点 |
| entry_time | prediction_time + 10 分钟 | 回放执行时点 |
| label_end_time | entry_time + holding_minutes | 计划标签结束 |
| label_available_time | 标签结束分钟的 close 已完成时 | label_end_time |
| label_entry_price / label_exit_price | 精确分钟边界已完成 Bar close，USDT／基础单位 | 分别为入场和结束分钟 |
| execution_price_proxy / terminal_execution_price_proxy | 精确执行分钟的 Bar open，USDT／基础单位 | 只供回放 |
| label_raw / label_rank / label_rank_zscore | 原始延迟收益／平均排名／排名标准化 | 该截面标签成熟后 |

`dataset.parquet` 为 `prediction_time,symbol` 双键。缺条记录、未成交分钟与缺字段保留缺失；无前向补价格。每个预测截面包含显式资产行，预测掩码来自当时 Bar 与分数。标签缺失与成熟性只影响监督和评价。缺失的 Funding mark 仅在账本以明确计数的已完成分钟价格作代理。缺少可靠的历史上市状态，九币为固定研究基准。
""",encoding="utf-8")
    (out/"training_protocol.md").write_text(f"""# 年度训练协议

模式 A 将 [{cfg['start']}, {cfg['end']}) 按年度划分为第一年训练与验证，以及第二年固定模型测试。验证占训练年度最后 {cfg['validation_months']} 个月。所有时间使用 UTC，开始包含、结束排除。每次拟合时 `feature_available_time <= prediction_time`，整组监督等待参与资产标签成熟且 `label_available_time <= cutoff`。验证与测试使用各自右边界限制完整评价区间。

Ridge 和 LightGBM 使用 MSE；MLP-IC 批次由完整时间横截面组成，目标为 1 减截面平均 Pearson 相关。Boosting 轮次与 MLP epoch 由验证集原始收益 IC 停止并确定。拟合期单独计算补值、去极值和标准化参数；年度重拟合用截至边界成熟的第一年标签。测试前保存、重载并核对预测一致。模型间以验证 Pearson IC 选择，冻结结果中三类模型完整报告。

模式 B 使用 `fit_years: 2`，每个截点向后一年固定预测，随后每年滚动。它需要三年完整数据。本轮单元测试验证日历与成熟性；完整三年真实实验未执行。
""",encoding="utf-8")
    comparison = pd.DataFrame(models)
    standard = comparison.loc[comparison.split.eq("historical_test")]
    selected = ", ".join(standard.loc[standard.selected_by_validation_ic,"model"].unique())
    summary = [f"| {r['model']} | {r['ic']:.6f} | {r['rank_ic']:.6f} | {r['valid_sections']} |"
               for r in standard.to_dict("records")]
    (out/"README.md").write_text(f"""# CRYPTO ALPHA FOUNDATION V1

## 验收

标准基准使用九币真实 Binance USDⓈ-M 行情，区间 [{cfg['start']}, {cfg['end']})，UTC。预测间隔 {cfg['interval_minutes']} 分钟，持有 {cfg['holding_minutes']} 分钟，延迟 {cfg['delay_minutes']} 分钟。通过 {tests['passed']} 项测试，另保存 {tests['integration']['real_label_examples']} 条真实样本，含全部因子、预测、目标权重与五个时钟。Model save/load 已核对，Nautilus 成交账本已对账。收益不作为验收门槛。

| 历史测试模型 | Pearson IC | Rank IC | 有效截面 |
|---|---:|---:|---:|
{chr(10).join(summary)}

按验证 IC 选出的模型为 {selected}。该历史年度在 V1/V2 已被观察。报告同时保留各模型、费用、资金费、换手、敞口和收益。执行证据等级为 BAR_EXECUTION_PROXY。

## 开源数值核对

Qlib Alpha158 登记全部 {parity['qlib_alpha158_formula_count']} 条上游表达式，其中 {parity['qlib_alpha158_daily_core_numeric_count']} 条日频公式实际执行数值对照。88 个分钟 Crypto 因子为既有研究公式。Qlib 与 Alphalens 原始 IC 已对照，Alphalens 的权重、收益、分位收益和成员换手已核对。VeighNa 分组滞后与最小名次百分位排名、FreqAI 半开时间切片与模型命名分别运行了原始源码单元。具体范围和源码版本见 `reproduction.csv`。V1 的 Qlib 官方股票基准为归档证据。

## 如何运行和替换模块

Windows 研究目录执行以下命令，作业由调度器在 Windows 会话外运行。

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File ./run_foundation_windows.ps1
```

独立研究虚拟环境依赖见 `requirements.txt` 和 `requirements-foundation-parity.txt`。配置副本用 `-Config` 传入，输出使用独立 `foundation_*` 子目录。

- 新因子实现一个 pandas Series 计算函数，通过 `Registry.register(Factor(...))` 声明名称、公式、字段、窗口及参数。插件提供 `register(registry)` 并列入 `factor_plugins`。注册后自动出现在数据集和因子评价中。选 `factor_set=all`、`train_screen` 或因子名单。
- 改 `interval_minutes` 切换 30m、1h、4h、8h，默认持有期跟随预测间隔。`holding_minutes` 可以另设；组合按预测间隔连续再平衡。
- 改 `models` 选择 Ridge、LightGBM、MLP-IC 和各自参数。三者共用标签及 IC 评价器。MSE 训练、直接 IC 优化与验证 IC 选模分别记录。
- 改 `portfolio` 可用 `rank`、`raw` 或 `equal`。运行 `.venv-cpu/Scripts/python.exe -m foundation.main --replay-only raw` 可直接复用冻结预测、价格与 Funding，无需重新训练。
- 变更 `fit_years`、首尾日期和验证月份控制年度协议。默认模式 A，模式 B 要求至少三年。

IC 按时点对同一可比资产对计算 Pearson 与 Spearman，再取均值。ICIR 用样本标准差 ddof=1。月度 IC、不同持有期 IC 与因子横截面相关表均保留。分位收益使用同截面平均收益去中心，成员换手按前一期分位成分与当前成分的差计。

标准组合升序平均排名、去均值、按总体标准差 ddof=0 标准化，再做绝对权重和为 1 的 L1 归一化，净敞口为 0。尺度在 L1 步骤抵消。无足够资产或零方差时输出零仓。缺失分数权重为零。额外幂变换及归一化公式待确认。

详见 `architecture.md`、`interface_registry.csv`、`dataset_schema.md`、`training_protocol.md` 与 `tests.json`。

Production orders = 0  
Demo orders = 0
""",encoding="utf-8")
