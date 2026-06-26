# 策略评价指标覆盖度审计（Strategy Evaluation Metric Coverage Audit）

本文审计 VWM 策略评价**指标体系是否全面**（非数据覆盖、非信号覆盖），并标注本阶段
新增了哪些可从现有 backtest outputs 可靠计算的指标，哪些仍为 planned/NA（不伪造）。

状态定义：
- **covered**：单实验/矩阵 builder 已产出（`build_crypto_perpetual_eval_table` / `..._matrix_eval_table`）。
- **added**：本阶段在 `build_strategy_batch_eval_table` 中新增计算（来自现有 summary/equity_curve/trades/matrix）。
- **planned/NA**：当前 outputs 不足以可靠计算，填 NA 并说明。

数据来源仅限：已有 `summary.json` / `equity_curve.csv` / `trades.csv` / `positions.csv` /
`matrix_evaluation_table.csv`。**不下载、不联网、不重跑回测。**

---

## A. 基础信息
covered：Strategy(由 --strategy 注入)、Market Type、Exchange、Symbol、Contract Type、Bar Type、
Window、Start、End、Days、Bars、Status。**added**：Failure Reason（缺标的/缺优选 cell 时填原因）。

## B. 收益
covered：Initial Cash、Final Equity、Net PnL、Total Return、Benchmark Return、Excess Return、
Zero Fee Return、Half Fee Return、VIP Fee Return、Break-even Fee Ratio。
**added（可从现有数据可靠计算）**：Annualized Return、Gross Return（=Net Without Commission 口径）、
Fee Drag（= Zero Fee Return − Total Return）、Calmar Ratio（= 年化/MaxDD%）、Return/Max Drawdown、
Best Day Return、Worst Day Return、Avg Daily Return、Daily Return Std（均由 equity_curve 按 UTC 日重采样）。

## C. 风险
covered：Max Drawdown %、Sharpe、Sortino、Volatility。
**added**：Max Drawdown（绝对值，equity 峰谷）、Downside Volatility（年化下行标准差，bar 级）。
**planned/NA**：VaR 95% / CVaR 95% / Tail Loss（需要更细的收益分布与口径约定）、
Drawdown Duration / Recovery Time（需要回撤区间标注；后续可从 equity_curve 计算）、
Max Consecutive Losing/Winning **Trades** → 已在 D 类用 trades 实现。

## D. 交易质量
covered：Trade Count、Fill Count、Long Trades、Short Trades、Win Rate、Profit Factor、
Avg Trade PnL、Avg Win、Avg Loss、Gross Profit、Gross Loss、Gross PnL。
**added**：Payoff Ratio（Avg Win/|Avg Loss|）、Expectancy（WinRate·AvgWin+LossRate·AvgLoss）、
Median Trade PnL、Best Trade、Worst Trade、Trade PnL（best/worst/median 已含）、
Max Consecutive Wins、Max Consecutive Losses（按 trades.csv realized_pnl 符号）。

## E. 持仓与暴露
covered：Exposure %、Long Exposure %、Short Exposure %、Flat %、Avg/Max Holding Time、Avg/Max Holding Bars。
**added**：Net Direction Bias（Long%−Short%）、Strategy Direction Bias（long/short/neutral）。
**planned/NA**：Long/Short Trade Ratio、Average/Max Position Size、Average/Max Notional Exposure
（当前 positions.csv 为空、equity_curve 的 position 为合约数而非稳定名义额，名义额口径不稳定 → NA）。

## F. 成本与换手
covered：Total Commission、Commission/Initial Cash、Commission/|Gross PnL|、Commission/|Net PnL|、
Avg Commission/Trade、Avg Commission/Fill、Turnover。
**added**：Net/Gross Ratio（Net PnL/Gross PnL）、Fee Drag（见 B）、Break-even Commission（=毛 PnL，>0 才有意义）。
**planned/NA**：Cost per Turnover（依赖稳定换手口径，后续可加）。

## G. 基准与相对表现
covered：Benchmark Return、Excess Return、Zero Fee Excess Return。
**added**：Benchmark Direction（up/down/flat）、Strategy Direction Bias。
**planned/NA**：Beta / Correlation / Information Ratio / Up-market / Down-market performance
（需要 daily 对齐的策略与基准收益序列做回归；当前 benchmark 仅取窗口首末收盘，未存 daily 基准序列 → NA，
后续从 equity_curve 的 close 列构造 daily 基准收益即可补齐）。

## H. 稳定性 / 鲁棒性（来自 matrix）
**added（从 matrix_evaluation_table 计算）**：Number of Windows Tested、Positive Return Windows、
Positive Excess Windows、Positive Excess Ratio、Mean Excess Across Windows、Std Excess Across Windows、
Best Window、Worst Window、Best Bar Type、Worst Bar Type。
说明：上述“windows”按该标的的**全部已测 cell（bar×window）**统计（BTCUSDT=9 格中实际有 equity/trades 的格）。
**planned**：Stability Score、Rank Consistency（需固定多策略/多标的后定义稳健评分）。

## I. 永续合约机制
covered：Funding Modeled=No、Margin Modeled=No、Liquidation Modeled=No、Mark Price Modeled=No、
Index Price Modeled=No。
**planned/NA（明确标记 No / NA，不伪造）**：Funding Data Available=No、Mark Price Data Available=No、
Funding-adjusted PnL / Funding-adjusted Return=NA（funding 未进入 PnL）。

---

## 小结

- **可从现有 outputs 可靠补齐**的指标已在 `build_strategy_batch_eval_table` 中新增（B/C/D/E/F/G/H 多项），
  并进入 88 行 pivot 评测表。
- **依赖稳定 daily 基准序列或名义额口径**的指标（Beta/Correlation/IR、Notional Exposure、VaR/CVaR、
  Drawdown Duration/Recovery、Funding-adjusted）暂列 **planned/NA**，在 `metric_coverage_audit.csv/md`
  中逐条标注，待后续补足数据/口径再计算，绝不伪造。
- 指标体系已覆盖：基础 / 收益 / 风险 / 交易质量 / 持仓暴露 / 成本 / 基准相对 / 矩阵稳定性 / 永续机制，
  足以支撑“单策略 × 多标的”的批量评价与标的筛选。
