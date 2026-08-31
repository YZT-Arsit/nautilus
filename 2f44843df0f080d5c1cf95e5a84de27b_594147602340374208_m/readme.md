实盘时间范围：start_date, until_date = "2025-10-18", "2026-06-15" （UTC时间）
资金增加时间：2025-10-18, 2026-01-10, 2026-04-13 

文件夹：
- 策略_保守
- 策略_激进
- 文件夹外部是两个策略等权的实盘结果

文件夹内容：
- backtest_net_ret_df.csv 我计算时，使用了产生交易决策之后的5分钟TWAP作为执行价格，实盘下的绩效为 ±1BP （没有使用很强的高频因子，也没有在秒级别以下抢订单，不代表我做算法交易的水平）
- backtest_position_df.csv 里面交易了9种活跃的数字货币`XRPUSDT,DOGEUSDT,SUIUSDT,BNBUSDT,ETHUSDT,BTCUSDT,1000PEPEUSDT,SOLUSDT,ADAUSDT`，不同币种也全部等权。
- cumulative_returns_已扣手续费5e-4.jpg 使用的账号有 -5e-4 的交易费率。这张图片画出来左图与右图，左图是表现top50% 的币种，右图是 bot50% 的币种，左右两图的黑色为所有品种的等权策略权益曲线（同一条），画成左右两图只是为了可视化方便查看。

  policy_name   anl_ret  SharpR  maxDD  turnover  ttl_ret  winRate  longWR  shortWR  longTR  shortTR  neutralTR  longRet  shortRet  neutralRet  cord_w001  cord_w003  cord_w005  cord_w010  cord_w020 
0   aggressive    1.316   3.038 -0.100     0.568    0.741    0.535   0.526    0.548   0.481    0.515      0.004    0.223     0.361      -0.002      0.045      0.031      0.102      0.038      0.034
1 conservative    2.013   4.572 -0.067     0.645    1.071    0.573   0.491    0.651   0.473    0.523      0.004    0.181     0.571      -0.002      0.028      0.097      0.075      0.019      0.041
2     COMBINED    1.573   4.086 -0.063     0.564    0.862    0.550   0.496    0.598   0.471    0.529      0.000    0.261     0.379       0.000      0.014      0.060      0.078      0.018      0.031

cord_w 表示计算仓位与未来一段时间价格变化方向的相关性，这个策略没有明显的反转或者趋势。w后面表示开启的窗口大小，3表示 3 * 15分钟。
这个策略的决策间隔是 15分钟。
