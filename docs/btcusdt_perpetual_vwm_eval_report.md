# BTCUSDT USD-M 永续合约 VWM 回测评测报告

## 1. 数据源

- 交易所：BINANCE
- 市场类型：USD-M 永续合约（futures_um / crypto_perpetual）
- 数据来源：**Binance Vision 公共只读归档**
  `data/futures/um/daily/klines/BTCUSDT/5m/`
- 访问方式：public read-only，**无 API key、无 private endpoint、无账户/余额/持仓/下单/撤单/杠杆/实盘**
- 导入脚本：`scripts/ingest_crypto_perpetual_bars.py`（逐日下载、规范化、schema 校验）

## 2. 数据日期 / 品种 / 周期

- 品种：BTCUSDT（instrument_id = `BTCUSDT-PERP.BINANCE`）
- K 线周期：5m
- 日期范围：2024-06-01 ~ 2024-06-07（7 天）
- 导入条数：**2016** 条 5m K 线（7 × 288），跨日时间戳单调、无重复，OHLC 边界与
  volume/trade_count 全部通过校验，`bar_source = trade_bar`

## 3. 策略

- 策略：VWM（数学逻辑未改动）
- 参数：`mom_len=5, avg_len=20, atr_len=5, atr_pct=0.5, setup_len=5`
- 执行：Nautilus 原生回测后端，`fill_timing=same_bar`，`fee_rate=0.0005`，
  `slippage_bps=1.0`，`initial_cash=100000`，允许做空

## 4. 回测命令

```bat
uv run --no-sync python scripts\run_vwm_batch_backtests.py ^
  --config configs\backtests\vwm_btcusdt_perpetual_5m_eval.yaml ^
  --out outputs\backtests\vwm_btcusdt_perpetual_5m_eval ^
  --fail-fast
```

评测表生成：

```bat
uv run --no-sync python scripts\build_crypto_perpetual_eval_table.py ^
  --backtest-dir outputs\backtests\vwm_btcusdt_perpetual_5m_eval
```

- 回测输出目录：`outputs/backtests/vwm_btcusdt_perpetual_5m_eval/`
- 评测表：`evaluation_table.csv` / `evaluation_table.md`
- 失败记录：`failures.csv`（仅表头，0 失败）

## 5. 指标表

| 指标 | 值 |
| --- | --- |
| Market Type | crypto_perpetual |
| Exchange | BINANCE |
| Symbol | BTCUSDT |
| Contract Type | USD-M perpetual |
| Bar Type | 5m |
| Start / End | 2024-06-01 / 2024-06-07 |
| Days / Bars | 7 / 2016 |
| Initial Cash | 100,000 |
| Final Equity | 95,937.40 |
| Net PnL | **-4,062.60** |
| Total Return | **-4.06%** |
| Annualized Return | -88.50%（短样本外推，仅供参考） |
| Max Drawdown | 6,180.87（按 equity 曲线峰谷，单位 USDT） |
| Max Drawdown % | 6.18% |
| Sharpe | -11.96（短样本年化，仅供参考） |
| Sortino | -9.04（短样本年化，仅供参考） |
| Volatility | 0.1796（短样本年化，仅供参考） |
| Trade Count | 33 |
| Fill Count | 66 |
| Long Trades | 0 |
| Short Trades | 33 |
| Win Rate | 15.15% |
| Profit Factor | 0.499 |
| Avg Trade PnL | -53.57 |
| Avg Win | 352.44 |
| Avg Loss | -126.07 |
| Total Commission | 2,294.90 |
| Commission / Gross PnL | 1.298 |
| Turnover | 45.90（成交名义 / 初始资金） |
| Status | success |

## 6. 主要观察

1. **本周期内 VWM 在 BTCUSDT 永续 5m 上为净亏损**：7 天 -4.06%，期末权益
   95,937。
2. **方向单一**：33 笔交易全部为 SHORT（Long Trades = 0），与该周 BTC 偏震荡/反弹
   行情不利于纯空头信号一致。
3. **胜率低、盈亏比 < 1**：胜率 15.15%，Profit Factor 0.499（平均盈利 352.44 但平均
   亏损 -126.07 且亏多胜少），整体期望为负。
4. **手续费占比极高**：Total Commission 2,294.90，**Commission / Gross PnL = 1.30**，
   即手续费已超过毛实现盈亏的绝对值。在 5m 高频 + 33 笔往返（66 笔成交）下，手续费
   是亏损的主要放大因素之一。毛实现亏损 -1,767.70，叠加手续费后净亏损 -4,062.60。
5. **回撤可控但样本极短**：最大回撤 6.18%（约 6,181 USDT）。Sharpe/Sortino/年化均为
   短样本年化外推，方向为负但**不具统计意义**。

## 7. Caveat（重要）

- 当前回测**未建模**永续合约的：**funding 资金费率、保证金、强平、mark/index price**。
- 年化收益、Sharpe、Sortino、波动率均来自 **7 天 intraday 样本**的年化外推，仅作框架
  连通性与信号初筛参考，**不构成绩效结论**。
- 指标表中无法可靠计算的字段一律填 `NA`，不做伪造。
- 本表用于**框架验证 + 策略初筛**，不可作为最终绩效判断或上线依据。

## 8. 下一步建议

1. **延长样本**：扩到数月（如 2024 全年）再看 VWM 在永续上的稳定性，短样本不可下结论。
2. **建模永续机制**：加入 funding / 保证金 / 强平后再评估真实净收益（funding 对持仓过夜
   尤其敏感）。
3. **手续费/换手敏感性**：当前 5m + 纯空头导致手续费吞噬收益，可评估更长周期（15m/1h）
   或更严格的入场过滤以降低换手。
4. **扩品种（模板已就绪）**：评测表按"行=品种"设计，可直接复用到 ETHUSDT / SOLUSDT /
   BNBUSDT（这些品种的永续 5m 数据已部分在库），形成多品种对比。
5. **不要**在当前短样本结果上调参或得出策略优劣结论。
