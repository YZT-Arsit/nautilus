# BTCUSDT USD-M 永续合约 VWM 回测评测报告

## 1. 数据源

- 交易所：BINANCE / 市场类型：USD-M 永续合约（futures_um / crypto_perpetual）
- 数据来源：**Binance Vision 公共只读归档** `data/futures/um/daily/klines/BTCUSDT/5m/`
- 访问方式：public read-only，**无 API key、无 private endpoint、无账户/余额/持仓/下单/撤单/杠杆/实盘**
- 导入脚本：`scripts/ingest_crypto_perpetual_bars.py`（逐日下载、规范化、schema 校验）

## 2. 数据日期 / 品种 / 周期

- 品种：BTCUSDT（`BTCUSDT-PERP.BINANCE`）/ 周期：5m / 范围：2024-06-01 ~ 2024-06-07（7 天）
- 导入条数：**2016** 条（7×288），时间戳单调无重复、OHLC 边界合法、`bar_source=trade_bar`

## 3. 策略

- VWM（数学逻辑未改动），参数 `mom_len=5, avg_len=20, atr_len=5, atr_pct=0.5, setup_len=5`
- 执行：Nautilus 原生回测，`fill_timing=same_bar`，`fee_rate=0.0005`，`slippage_bps=1.0`，
  `initial_cash=100000`，允许做空

## 4. 回测命令

```bat
uv run --no-sync python scripts\run_vwm_batch_backtests.py ^
  --config configs\backtests\vwm_btcusdt_perpetual_5m_eval.yaml ^
  --out outputs\backtests\vwm_btcusdt_perpetual_5m_eval --fail-fast
```

评测表生成（只读已有 backtest/data，不联网、不重跑回测）：

```bat
uv run --no-sync python scripts\build_crypto_perpetual_eval_table.py ^
  --backtest-root outputs\backtests\vwm_btcusdt_perpetual_5m_eval ^
  --data-root historical_data\market_data ^
  --out-dir outputs\backtests\vwm_btcusdt_perpetual_5m_eval ^
  --symbol BTCUSDT --exchange BINANCE --venue-type futures_um --bar-type 5m ^
  --start 2024-06-01 --end 2024-06-07 --vip-fee-ratio 0.2 --half-fee-ratio 0.5
```

- 回测输出：`outputs/backtests/vwm_btcusdt_perpetual_5m_eval/`（summary / failures / 各 job 文件）
- 评测表：`evaluation_table.csv`（全量列）/ `evaluation_table.md`（核心列）
- failures.csv：仅表头，**0 失败**

## 5. 核心指标表

| 指标 | 值 |
| --- | --- |
| Market / Exchange / Symbol | crypto_perpetual / BINANCE / BTCUSDT |
| Contract / Bar | USD-M perpetual / 5m |
| Start / End / Days / Bars | 2024-06-01 / 2024-06-07 / 7 / 2016 |
| Initial Cash / Final Equity | 100,000 / 95,937.40 |
| Net PnL / Total Return | -4,062.60 / **-4.06%** |
| **Benchmark Return（买入持有）** | **+2.51%** |
| **Excess Return（超额）** | **-6.57%** |
| Max DD % | 6.18% |
| Trades / Long / Short | 33 / 0 / 33 |
| Win Rate / Profit Factor | 15.15% / 0.499 |
| Gross PnL / Profit / Loss | -1,767.70 / +1,762.20 / -3,529.90 |
| Total Commission | 2,294.90 |
| Commission / Gross PnL | 1.298 |
| Commission / Initial Cash | 2.29% |
| Commission / \|Net PnL\| | 56.5% |
| Exposure % / Short / Long / Flat | 40.97% / 40.97% / 0% / 59.03% |
| Avg / Max Holding | 125.2 min（25.0 bars）/ 625 min（125 bars） |
| Status | success |

> Sharpe/Sortino/Volatility/Annualized 为 7 天短样本年化外推（见 CSV），仅供参考。

## 6. Benchmark comparison（基准对比）

- 基准：同周期 BTCUSDT 永续 5m **close-to-close 买入持有**，由 `historical_data` 下的 5m
  parquet 读取首/末收盘价计算。
- **Benchmark Return = +2.51%**，策略 **Total Return = -4.06%** → **Excess Return = -6.57%**。
- 即使在零手续费情景下（-1.77%），相对基准的 **Zero Fee Excess Return = -4.27%**，仍跑输买入持有。
- 解读：本周 BTC 小幅上行（+2.5%），而 VWM 全程只做空（见第 8 节），方向与行情相反，是跑输
  基准的主因。

## 7. Fee sensitivity analysis（手续费敏感性）

> **手续费情景用于敏感性分析，不代表具体账户等级。真实手续费取决于交易所、VIP 等级、
> maker/taker、返佣和活动费率。**

| Scenario | Commission | Net PnL | Total Return |
| --- | --- | --- | --- |
| Actual（回测费率，taker 0.05%） | 2,294.90 | -4,062.60 | -4.06% |
| Zero fee | 0 | -1,767.70 | -1.77% |
| Half fee（×0.5） | 1,147.45 | -2,915.15 | -2.92% |
| VIP fee（×0.2，*illustrative*） | 458.98 | -2,226.68 | -2.23% |

- **Net Without Commission（=毛实现 PnL）= -1,767.70**；Gross Profit +1,762.20，Gross Loss -3,529.90。
- **Break-even Fee Ratio = 0**：因为毛实现 PnL ≤ 0，**即使手续费为零，策略本周仍亏损**，
  不存在“某个更低费率即可盈利”的临界点。
- **Zero Fee Profitable = No**。
- **Fee Sensitivity Note**：*zero-fee still unprofitable (gross PnL ≤ 0): likely a signal-quality
  issue, not pure cost*。

## 8. Exposure and holding behavior（敞口与持仓时长）

- 由 `equity_curve.csv` 的逐根 `position` 与 `trades.csv` 的进出场时间计算：
- **Exposure 40.97%**（持仓占比），其中 **Short 40.97% / Long 0% / Flat 59.03%** —— 全程纯空头、
  超过一半时间空仓。
- **Avg Holding 125.2 分钟（25.0 根 5m bar）**，**Max Holding 625 分钟（125 根）**。
- 解读：纯空头 + 中等持仓时长，在上行周内自然承压；方向暴露是亏损来源，而非高频抖动。

## 9. Perpetual mechanism caveats（永续机制状态）

| 机制 | 是否建模 |
| --- | --- |
| Funding（资金费率） | **No** |
| Margin（保证金） | **No** |
| Liquidation（强平） | **No** |
| Mark / Index Price | **No** |

- 当前 PnL **未包含** funding / 保证金 / 强平 / mark-index 影响。对持仓过夜与高杠杆，funding 与强平
  尤其敏感。
- 因此本评测表用于**框架验证与策略初筛**，**不构成最终绩效结论**。

## 10. Interpretation update（结论更新）

- **方向问题是首要矛盾**：策略本周纯空头，基准 +2.51% 而策略 -4.06%，超额 -6.57%；方向与行情
  相反是亏损主因。
- **手续费应作为敏感性而非定论**：在当前默认手续费设定下，手续费压力较高（Commission/Gross PnL
  =1.30，Commission/\|Net\|=56.5%）；因此需要通过零手续费、低手续费和 VIP 情景进行敏感性分析。
  **本例中，零手续费情景下策略仍亏损（-1.77%）且跑输基准（超额 -4.27%），说明问题更可能来自
  信号质量，而非单纯交易成本**；若在低费率下显著改善则说明对成本高度敏感——但本周数据不属于
  这种情形。
- **样本极短**：7 天 intraday，年化/Sharpe/Sortino/波动率均为外推，不具统计意义。

## 11. 下一步建议

1. **优先排查信号方向/质量**：零手续费下仍亏损且跑输基准，说明先解决 VWM 在该周期的方向暴露，
   而非先优化手续费。
2. **延长样本**（数月→全年）再评估，短样本不可下结论。
3. **建模永续机制**（funding / 保证金 / 强平）后再看真实净收益。
4. **多品种扩展（模板已就绪）**：评测表按“行=品种”设计，可直接扩 ETHUSDT/SOLUSDT/BNBUSDT。
5. **不要**在当前短样本上调参或得出策略优劣定论。
