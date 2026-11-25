# CRYPTO ALPHA V1

## 技术实现

- Windows 模块目录 `D:\nautilus\research\crypto_alpha`，主实验产物 `D:\nautilus\outputs\crypto_alpha\v1`。Mac 的 Nautilus 项目使用相同相对路径。研究模块通过独立 Git 提交和 Tailscale 传输，Windows 按文件 SHA-256 验收。
- 通过 Nautilus Hive 分区接口读取 Binance USDⓈ-M 永续 1m StandardBar、结算率和历史 mark。原始档案只读。已有分区优先，缺失预热日期和缺失交易日的补充分区使用 Nautilus 原导入器产出至 `outputs/crypto_alpha/supplemental_market_data`。正式运行保留数据指纹、覆盖和分区清单。
- 每根 1m Bar 以开盘分钟标识，成交聚合字段在该分钟结束时可用。Label 用入场、结束时刻刚完成的 Bar 的收盘价。执行用计划入场分钟开盘价作为 `BAR_EXECUTION_PROXY`。Funding 按持仓、官方结算时间、rate 和 mark 计提。以整组跨时点提交来复用 Nautilus 成交模拟器和账本。
- 训练区间的最后时刻按 Label 成熟时间截断。横截面平均排名后，以 `ddof=0`、`epsilon=1e-12` 标准化，至少 3 个有效资产。保留原始收益、排名、排名 Z-score 与收益 Z-score。因子缺失保持空值，训练预处理只以拟合数据估算分位剪裁、填补和标准化。预测资格只由预测当时已完成的成交 Bar 决定。
- 按每个时间截面计算 Pearson IC 与 Spearman Rank IC，以有效时点均值报告。ICIR 为逐时 IC 均值除以样本标准差，无年化。MLP-IC 的训练目标是逐截面 Pearson IC 的均值，验证和样本外另行报告对原始收益及标准化标签的 Pearson IC。
- 模型与因子集由拟合年度内部验证的原始收益 Pearson IC 选择。Ridge 比较三个正则强度，LightGBM 比较两组树参数，MLP-MSE/MLP-IC 共享一套小网络结构。每个家族固定参数后以第一年所有已成熟样本重新拟合一次。第二年使用冻结模型。设置 `fit_years=2` 可运行两年拟合后逐年重训，输入区间至少三年。
- 主组合按预测排序居中、标准化，再做中性 L1 分配。单币上限 30%，可行时毛敞口 1，资产较少且无法同时满足上限和目标毛敞口时缩减双侧。多空方向由预测分数决定。执行缺价且涉及已有持仓或非零目标时跳过整组再平衡，估值保留最后已观测价格并计数。Beta 是 30m 收益滚动估计加权后的暴露。
- 本金 10,000 USDT，单边手续费 4 bps、点差 1 bp、滑点 1 bp。成本敏感性按已成交名义额用固定成交路径计算。总 BE 为价格 PnL 加 Funding 再除以实际成交额，手续费 BE 先扣除点差和滑点。额外成本承受能力使用当前净 PnL 除以成交额并下限置零。日均换手使用单次成交额除以调仓前上一期权益再按 UTC 日汇总。净收益为最终权益相对初始本金的变化，最大回撤按计划调仓时点权益计算。固定本金的 1× 简单累计收益使用独立的固定名义本金路径，该诊断可以低于 −100%。
- 信号接口 `nautilus_targets.parquet` 包含冻结模型的预测时间、执行时间、模型版本、合约、预测分数、相对强度和目标权重。离线生成。Production orders 和 Demo orders 均为 0。

## 实验设置

- 正式区间 `[2024-07-01, 2026-07-01)` UTC。第一年内部切分点 `2025-04-01`，主模型训练截止与测试起点 `2025-07-01`。
- 九币固定池 BTCUSDT、ETHUSDT、SOLUSDT、BNBUSDT、XRPUSDT、DOGEUSDT、ADAUSDT、SUIUSDT、1000PEPEUSDT。预先指定的固定池具有选择偏差和幸存者偏差。有效横截面最多 9 个资产。
- 主时钟 60 分钟预测、延迟 10 分钟、持有 60 分钟。比较 30、240、480 分钟，持有期与各自预测间隔相等。
- 保存验证和冻结测试指标，包括负 IC 和亏损结果。查看 `model_comparison.csv`、`frequency_comparison.csv`、`summary.png`。因子公式和质量诊断见 `factor_registry.csv`、`factor_ic.csv`、`factor_rank_ic.csv`、`factor_correlation.csv`。
- 源码版本和复现证据见 [references/reproduction.csv](references/reproduction.csv)，已有接口与新适配点见 [references/interface_reuse.csv](references/interface_reuse.csv)。

## 运行命令

Windows PowerShell，在模块目录运行。

```powershell
python -m venv .venv-cpu
.venv-cpu\Scripts\python.exe -m pip install -r requirements.txt
.venv-cpu\Scripts\python.exe -B -m pytest -q
..\..\.venv\Scripts\python.exe -B data\supplement.py
.venv-cpu\Scripts\python.exe -B training\prepare.py
.venv-cpu\Scripts\python.exe -B training\sanity.py
.venv-cpu\Scripts\python.exe -B training\run.py
.venv-cpu\Scripts\python.exe -B tests\verify_artifacts.py
```

Windows 可用计划任务独立运行。该任务使用本机账户 S4U，SSH 断开后继续，检测已完成频率并续跑。默认保留 12 GiB D 盘空闲空间，开始阶段及每个频率前测量。

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\run_windows.ps1 -Install -Start
Get-ScheduledTaskInfo -TaskName CryptoAlphaV1Research
```

Mac 在模块目录同步源码。同步前提交变更，远端只写研究模块目录。

```sh
python3 sync_windows.py --config configs/windows-sync.example.json
.venv/bin/python -m pytest -q tests
.venv/bin/python training/reproduce.py --output ../../outputs/crypto_alpha/v1/reproduction
```

## 未完成事项

- 当前主实验使用 1m 开盘价格代理。深度、原生 L1 成交、合约名义和数量步长约束、流动性容量约束、逐笔执行和 MAKER 回放留在后续适配。
- 已有分钟源的主动买卖字段在部分交易对或日期缺失。未构造缺失的买卖量。Premium、Basis 和 OI 缺少已验证的逐时可用档案。
- 九合约的历史挂牌时间和逐时交易状态快照不完整，覆盖报告记录可证实的最早档案分区和有效成交 Bar。
