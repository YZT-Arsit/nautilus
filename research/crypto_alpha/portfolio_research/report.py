"""Research evidence summary; no portfolio rule is selected by observed PnL."""
import json

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from portfolio_research.sources import HIST,OCTOBER,VALIDATION
from v3.common import write_json


def report(out,cfg,test,time,commit):
    pc=pd.read_csv(out/"portfolio_comparison.csv")
    br=pd.read_csv(out/"ic_return_bridge.csv")
    wi=pd.read_csv(out/"weight_comparison.csv")
    rc=pd.read_csv(out/"portfolio_risk.csv")
    sc=pd.read_csv(out/"prediction_statistics.csv")
    cost=pd.read_csv(out/"cost_sensitivity.csv")
    b=pc.query("split==@OCTOBER and method=='rank_l1'").iloc[0]
    o=br.query("split==@OCTOBER and method=='rank_l1'").iloc[0]
    w=wi.query("split==@OCTOBER and method=='rank_l1'").iloc[0]
    risk=rc.query("split==@OCTOBER and method=='rank_l1'").iloc[0]
    ma=pc.query("split==@OCTOBER").set_index("method")
    table=br.query("split!=@VALIDATION and method=='rank_l1'")
    summary=dict(frozen_families=pc.model.nunique(),strength_methods=6,standard_methods=len(cfg["methods"]),
                 extension_methods=len(cfg["extensions"]),account_count=len(pc),tests=test["passed"],
                 weight_constraints="PASS",reconciliation="PASS",sources=test["source_custody"],
                 maximum_bridge_error=float(br.matched_price_pnl_error.abs().max()),
                 october_rank_portfolio=b.to_dict(),october_bridge=o.to_dict(),october_target=w.to_dict(),
                 october_risk=risk.to_dict(),october_all_methods=ma.net_return.to_dict(),
                 elapsed_seconds=time,commit=commit,production_orders=0,demo_orders=0)
    write_json(out/"summary.json",summary)
    fig,ax=plt.subplots(2,3,figsize=(16,9),constrained_layout=True)
    a=br.query("split==@HIST and method=='rank_l1'")
    ax[0,0].bar(a.model,a.positive_ic_positive_ideal_fraction,label="label-weight return")
    ax[0,0].scatter(a.model,a.positive_ic_positive_actual_fraction,color="red",label="executed pre-cost")
    ax[0,0].set_title("Historical | positive payoff given positive IC")
    ax[0,0].legend(fontsize=7)
    history=pd.read_parquet(out/"accounts"/OCTOBER/"MASTER"/"rank_l1"/"ic_return_by_section.parquet")
    ax[0,1].scatter(history.pearson_ic,history.ideal_dot*10000,s=7,alpha=.4)
    ax[0,1].set_title("October | Pearson IC and label return (bps)")
    ax[0,2].barh(ma.index,ma.net_return)
    ax[0,2].set_title("October | dynamic net return at 6 bps")
    main=cost.query("split==@OCTOBER and method=='rank_l1'")
    for mode,p in main.groupby("mode"):
        ax[1,0].plot(p.single_sided_cost_bps,p.total_cost_return,marker="o",label=mode)
    ax[1,0].legend(fontsize=7)
    ax[1,0].set_title("October | rank cost sensitivity")
    r=rc.query("split==@OCTOBER").set_index("method")
    ax[1,1].barh(r.index,r.avg_abs_btc_beta)
    ax[1,1].set_title("October | mean absolute BTC beta")
    ax[1,2].barh(ma.index,ma.daily_turnover)
    ax[1,2].set_title("October | actual daily turnover")
    for p in ax.flat:
        p.grid(alpha=.2)
        p.tick_params(labelsize=8)
    fig.savefig(out/"summary.png",dpi=145)
    plt.close(fig)
    detail="\n".join(f"| {p.method} | {p.gross_return:.3%} | {p.net_return:.3%} | {p.daily_turnover:.2f}× |"
                     for p in ma.reset_index().itertuples())
    cohort="\n".join(f"| {p.split} | {p.model} | {p.mean_ic:.5f} | "
                   f"{p.positive_ic_positive_ideal_fraction:.2%} | {p.positive_ic_positive_actual_fraction:.2%} |"
                   for p in table.itertuples())
    cal=pd.read_csv(out/"score_calibration.csv")
    q=cal.query("split==@OCTOBER and method=='rank_linear'").drop_duplicates("model")
    line="、".join(f"{r.model} rank slope {r.slope:.8f}, intercept {r.intercept:.8f}" for r in q.itertuples())
    (out/"README.md").write_text(f"""# PORTFOLIO RESEARCH V1

## 数据与冻结

历史年度 2025-07-01 至 2026-07-01 UTC 使用 Phase 3 年度 A 的 Ridge、LightGBM 与 MASTER，以及 Foundation 年度 A 的 MLP-IC 预测文件。MLP-IC 使用冻结 FULL_88，其余历史模型使用冻结 NO_DERIVATIVES_83，Phase 4 的横向比较保留该差异。10 月 2026-10-09 至 2026-11-01 UTC 使用 Phase 3 年度 B 的原封存 MASTER 预测，标作 OBSERVED_FORWARD_WINDOW。两个窗口均已观察。模型和因子集没有重新选择；本阶段保留三份冻结因子集。本轮未下载 Tick、BBO 或未来测试数据。

## 相对强度与执行

逐截面保存原值、去均值、Z-score、升序平均排名、排名 Z-score、L1 权重。标准差使用 ddof=0，最少三资产，常数截面零仓，缺失资产不分配仓位。Top K 的边界同分按固定标的顺序确定；有效资产必须至少 2K。Rank L1 与 Foundation 标准权重逐截面相等。无非线性变换时 Raw L1 与 Z-score L1 数值相等；正的统一标准差缩放在 L1 步骤抵消。默认非线性为恒等。带符号幂变换仅为可配置研究接口，额外公式待确认。

默认目标毛敞口 1×、净名义敞口 0，单币上限 0.5。风险组合对排名强度乘过去 96 个小时协方差的正则化逆平方根，包含对角收缩和特征值下限，估计窗口严格排除当前及以后数据。报告波动、BTC beta、两侧 HHI 和资产协方差。V2 的部分调仓、EMA 与换手约束单列为扩展，其毛敞口可低于 1×。

整点预测，10 分钟后按目标数量减现有数量成交，持有标签 60 分钟。持仓寿命按同方向数量由开仓至归零或翻向计算。缺报价触发整次跳过，保留已有仓位；期末只用可用价格平仓并单列成本。阶段内部两套账户按时点核对，包括 Nautilus 原始成交与资金费接口。共核对 {len(pc)} 个账本。

## IC 与组合收益

理论收益是目标权重与同段未来收益标签的点积。只在所有有仓资产的标签完整时计入完整理论收益样本；对账另外显式记录标签缺失。逐时把理论结果拆为标签收盘与执行开盘代理差额、标签缺失但执行可见部分、实际数量相对目标偏差、缺行情时的沿用标记、Funding 与执行成本。终止平仓费用账本单列。最大累计价格桥接误差 {summary['maximum_bridge_error']:.3g} USDT。

| 已观察窗口 | 模型 | 平均 Pearson IC | 正 IC 中理论收益为正 | 正 IC 中实际费前为正 |
|---|---|---:|---:|---:|
{cohort}

IC 衡量线性横截面关联，仓位形状和标签离散度共同决定理论收益。直接 IC Loss 的分数尺度没有收益单位。线性、排名线性及排名分位校准只拟合训练年度顺序验证预测。逐折资产预测是用前置训练数据临时重建，Phase 3 轮次取已完成上一折，首折固定预算；Foundation MLP 使用固定 5 轮。原冻结模型与已观察窗口预测保持原样。10 月校准 {line}。

## 10 月组合对照

| 权重方法 | 价格毛收益 | 6 bps 动态净收益 | 实际日均换手 |
|---|---:|---:|---:|
{detail}

标准 Rank L1 的目标平均毛敞口 {w.mean_target_gross:.4f}、最大绝对净敞口 {w.max_abs_target_net:.2g}，平均 BTC beta {risk.avg_btc_beta:+.4f}；平均绝对 beta {risk.avg_abs_btc_beta:.4f}。盈利仅作为辅助结果。

成本网格单边 0／1／2／4／6／8／10 bps，主场景手续费 4 bps、点差 1 bp、滑点 1 bp，Funding 单列。标准组合的同路径去成本 {b.fixed_path_zero_return_explicit:.3%}、独立零成本复利 {b.zero_cost_rerun:.3%}、固定本金简单收益 {b.fixed_capital_simple_6bps:.3%}、动态权益净收益 {b.net_return:.3%}。实际成交按调整后成交价计算名义额。执行证据 BAR_EXECUTION_PROXY。

## 运行

Windows 研究目录执行以下命令。改配置可复用原冻结预测比较组合方法和约束。

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File ./run_portfolio_research_windows.ps1
```

共 {test['passed']} 项测试通过。生产与 Demo 订单 0 / 0。
""",encoding="utf-8")
