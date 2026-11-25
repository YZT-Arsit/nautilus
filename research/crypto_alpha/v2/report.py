"""Assemble audited V2 evidence and requested figures."""
import json

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd

from v2.common import setup
from strategy_framework.execution.backtest_report import write_artifact_manifest


def matching(frame,key):
    for col in ("model","loss","prediction_minutes","label_minutes"):
        frame=frame.loc[frame[col]==key[col]]
    if "variant" in key and pd.notna(key["variant"]):
        frame=frame.loc[frame.variant==key["variant"]]
    if len(frame)!=1:
        raise ValueError(f"expected one frozen row, got {len(frame)} for {dict(key)}")
    return frame.iloc[0]


def plot(out,old,replay,cost,best):
    decay=pd.read_csv(out/"ic_decay.csv")
    frozen=decay.loc[(decay.origin=="V1_FROZEN_SCORE")&(decay.prediction_minutes==60)]
    signal=pd.read_parquet(out/"v1_signal_economics.parquet")
    ledger=pd.read_parquet(out/"model_portfolio_ledger.parquet")
    fig,ax=plt.subplots(2,3,figsize=(16,9),constrained_layout=True)
    fig.suptitle("Crypto Alpha V2 | Frozen historical study")
    for model in ("LightGBM","MLP-IC"):
        d=frozen.loc[frozen.model==model].sort_values("label_minutes")
        ax[0,0].plot(d.label_minutes,d.ic,marker="o",label=model)
        ax[0,0].fill_between(d.label_minutes,d.ic_ci95_low,d.ic_ci95_high,alpha=.15)
        d=signal.loc[(signal.interval==60)&(signal.model==model)]
        ax[0,1].plot(d.prediction_time,d.weighted_label_return.cumsum(),label=model)
    ax[0,0].set_xscale("log")
    ax[0,0].legend()
    ax[0,0].set_title("Frozen IC decay | 7-day block interval")
    ax[0,1].legend()
    ax[0,1].set_title("1h score-weighted cumulative Label return")
    for c in ("prediction_minutes","label_minutes","model","loss","variant"):
        ledger=ledger.loc[ledger[c]==best[c]]
    ax[0,2].plot(ledger.execution_time,(ledger.gross_pnl+ledger.funding).cumsum()/10000,label="same fills before costs")
    ax[0,2].plot(ledger.execution_time,ledger.equity/10000-1,label="6 bps net")
    ax[0,2].legend()
    ax[0,2].set_title("Validation-selected portfolio")
    daily=ledger.groupby(ledger.execution_time.dt.floor("D")).turnover_equity.sum()
    ax[1,0].plot(daily.index,daily)
    ax[1,0].set_title("Daily executed turnover / equity")
    for name,d in cost.loc[cost.origin=="V2_MODEL_COST_WINNER"].groupby("basis"):
        ax[1,1].plot(d.bps,d.net_return,marker="o",label=name)
    ax[1,1].legend()
    ax[1,1].set_title("Single-side all-in cost sensitivity")
    d=old.loc[old.split=="frozen_historical"]
    ax[1,2].scatter(d.daily_turnover,d.net_return,label="V1 scores")
    ax[1,2].scatter(replay.daily_turnover,replay.net_return,alpha=.60,label="V2 scores")
    ax[1,2].legend()
    ax[1,2].set_title("6 bps return versus executed turnover")
    for item in ax.flat:
        item.axhline(0,color="grey",lw=.6)
        item.grid(alpha=.2)
        item.tick_params(labelsize=8)
    fig.savefig(out/"summary.png",dpi=155)
    plt.close(fig)


def report(cfg,dates,v1,out):
    chosen=json.loads((out/"frozen_selection.json").read_text())
    device=json.loads((out/"run_environment.json").read_text())
    prior=pd.read_csv(out/"v1_reconciliation.csv")
    old=pd.read_csv(out/"portfolio_comparison.csv")
    models=pd.read_csv(out/"model_comparison.csv")
    replay=pd.read_csv(out/"model_portfolio_replay.csv")
    validation=models.loc[models.split=="validation"]
    historical=models.loc[models.split=="frozen_historical"]
    assert set(prior.interval)=={30,60,240,480} and prior.pass_reconciliation.all()
    assert prior.max_ledger_error_usdt.max()<2e-6
    assert (pd.to_datetime(historical.last_label_time,utc=True)<=dates["annual_cutoff"]).all()
    assert (replay.unsettled_funding==0).all()
    assert (replay.terminal_gross.abs()<1e-10).all()
    iw=chosen["ic_winner"]
    cw=chosen["cost_winner"]
    it=matching(historical,iw)
    ct=matching(replay,cw)
    assert ct.selected_cost_winner
    coverage=pd.read_csv(out/"post_v1_coverage.csv")
    covered=int(((coverage.kind=="1m_bar")&(coverage.partition_days>0)).sum())
    hold=f"NOT_EXECUTED: {covered}/9 assets covered after cutoff; no untouched annual nine-asset window"
    results=[dict(**row,experiment=case,sample=sample,use_for_selection=sample=="validation")
             for case,a,b in (("IC",iw,it.to_dict()),("COST",cw,ct.to_dict()))
             for sample,row in (("validation",a),("frozen_historical",b))]
    results.append(dict(experiment="Untouched annual",sample="reserved_metadata_only",status=hold,use_for_selection=False))
    pd.DataFrame(results).to_csv(out/"validation_results.csv",index=False)
    frequencies=[]
    for _,group in validation.groupby(["prediction_minutes","label_minutes"]):
        best=group.loc[group.ic.idxmax()]
        metrics=matching(replay.loc[replay.selected_for_model],best)
        frequencies.append(dict(**metrics.to_dict(),origin="V2",validation_ic=best.ic,
                                sample_status="V1_PREVIOUSLY_OBSERVED"))
    legacy=pd.read_csv(v1/"frequency_comparison.csv")
    for minutes,group in legacy.groupby("interval_minutes"):
        a,b=(group.loc[group.split==s].iloc[0] for s in ("validation","test"))
        cols="model ic rank_ic gross_return net_return max_dd sharpe be_bps daily_turnover".split()
        frequencies.append(dict(**b[cols].to_dict(),prediction_minutes=minutes,label_minutes=minutes,rebalance_minutes=minutes,
                                origin="V1_BASELINE",variant="v1_rank",validation_ic=a.ic,sample_status="V1_PREVIOUSLY_OBSERVED"))
    pd.DataFrame(frequencies).to_csv(out/"frequency_comparison.csv",index=False)
    fees=pd.read_csv(out/"cost_sensitivity.csv")
    if "origin" in fees:
        fees=fees.loc[fees.origin=="V1_FROZEN_SCORES"]
    new=pd.read_csv(out/"v2_model_cost_sensitivity.csv")
    fee_grid=pd.concat([fees.assign(origin="V1_FROZEN_SCORES"),new.assign(origin="V2_MODEL_COST_WINNER")],ignore_index=True)
    fee_grid.to_csv(out/"cost_sensitivity.csv",index=False)
    plot(out,old,replay,fee_grid,ct)
    begin=prior.loc[(prior.interval==60)&prior.model.eq("LightGBM")&prior.weight_method.eq("rank")].iloc[0]
    registry=pd.read_csv(out/"factor_registry.csv")
    n=int((registry.implementation_version=="v2").sum())
    positive=replay.loc[(replay.net_return>0)&(replay.trades>0)]
    observed_positive="；".join(f"{r.model}/{r.loss}/{r.variant}，净收益 {r.net_return:.3%}、总 BE {r.be_bps:.3f} bps、Sharpe {r.sharpe:.3f}"
                              for r in positive.itertuples()) or "无"
    light=old.loc[old.selected & old.split.eq("frozen_historical") & old.interval.eq(240) & old.model.eq("LightGBM")].iloc[0]
    text=f"""# CRYPTO ALPHA V2

## V1 复算

四种频率的冻结账本全部逐时对账通过。V1 manifest 内文件已按 SHA-256 校验。最大账本绝对差 {prior.max_ledger_error_usdt.max():.3g} USDT。复核预测、特征可用、入场及 Label 结束时间、成交数量差、Funding 符号、手续费和名义金额。

|1h LightGBM 口径|收益|
|---|---:|
|A 原成交路径去成本，含 Funding|{begin.A_same_fills_cost_removed:.4%}|
|B 零成本重跑，按动态权益计算仓位|{begin.B_zero_dynamic_replay:.4%}|
|C 固定本金的 6 bps 简单累计|{begin.C_fixed_notional_simple_6bps:.4%}|
|D 原动态权益的 6 bps 净收益|{begin.D_dynamic_net_6bps:.4%}|

实际 6 bps 路径价格盈亏为 {begin.gross_return:.4%}。等本金目标权重与同窗 Label 点积累计为 {begin.weighted_label_return_sum:.4%}，以实际每期建仓前权益加权后为 {begin.actual_equity_scaled_label_pnl:.2f} USDT。改用开盘执行代理产生 {begin.open_clock_proxy_pnl_gap:.2f} USDT 差异，目标到实仓剩余差 {begin.drift_or_execution_skip_pnl_gap:.3g} USDT，Funding 为 {begin.funding:.2f} USDT。实际权益较高的早期亏损权重较大。平均 IC 只衡量各横截面的相关性，组合收益还由实际收益幅度、权重和成交规模决定。

逐时诊断保留 IC、离散度、篮子收益、权重点积和实仓差异。分箱校准用于诊断，成本优化所用的校准使用拟合样本。IC 衰减覆盖 30m、1h、2h、4h、8h、24h，保留月度值、信号延续性及 7 日期块 bootstrap。经济账本保留调仓与未成交原因、持仓持续时间、绝对成交额、Funding 和费用。

## V2 实验与结果

- 2024-07-01 至 2025-07-01 拟合和选择，按 2025-04-01 切分验证，在年度截止时重拟合。2025-07-01 至 2026-07-01 是 V1 已观察期。所有 V2 配置先冻结，再回放该段历史。
- 期末后实盘档案目录只有 BTC、ETH、SOL 各 56 天分区。这段只检查分区清单，留待独立验证。保持九币年度口径，未执行真正未见测试。
- 保留 59 个原因子并新增 {n} 个。GRU、StockMixer、MASTER 均运行了 MSE 和截面直接 Pearson IC Loss。StockMixer 与 MASTER 为轻量架构移植，原股票工作流未运行。归一化 Label IC 单独报告。
- 验证 IC 冠军 {it.model} / {it.loss}，预测 {int(it.prediction_minutes)}m，Label {int(it.label_minutes)}m。验证 IC {iw['ic']:.6f}，冻结历史原始收益 IC {it.ic:.6f}、Rank IC {it.rank_ic:.6f}。同频重平衡毛收益 {it.gross_return:.2%}、6 bps 净收益 {it.net_return:.2%}、日均换手 {it.daily_turnover:.3f}×、总 BE {it.be_bps:.3f} bps。
- 验证净收益选中的活跃交易候选是 {ct.model} / {ct.loss} / {ct.variant}，预测 {int(ct.prediction_minutes)}m，Label {int(ct.label_minutes)}m，计划再平衡 {int(ct.rebalance_minutes)}m。验证净收益 {cw['net_return']:.2%}，冻结历史毛收益 {ct.gross_return:.2%}、净收益 {ct.net_return:.2%}、日均换手 {ct.daily_turnover:.3f}×、总 BE {ct.be_bps:.3f} bps。
- 全部冻结历史里录得正净收益的活跃 V2 候选为 {observed_positive}。该清单是历史诊断，主交易配置仍按验证期选择。
- 冻结 V1 分数测试 {old.variant.nunique()} 种建仓规则。4h LightGBM 由验证选中 {light.variant}，6 bps 净收益 {light.net_return:.2%}，日均换手 {light.daily_turnover:.3f}×。保留无交易候选并报告为零风险敞口。
- 预测延迟 10 分钟。Label 按配置定义，计划再平衡可低频于预测，组合继续按预测频率估值。风险退出和期末结清单列。换手限制使用交易前漂移仓位。资格或约束触发的必需调仓优先。成本优化使用带约束的近端迭代近似。
- 主成本 4 bps 手续费、1 bp 点差、1 bp 滑点。Funding 单独核算。0/1/2/4/6/8/10 bps 分别保存固定成交路径的敏感性与跨成本动态重跑。BAR_PROXY 证据，尚无同一窗口的九币 L1 回放。

## 执行

Windows 计划任务独立于 SSH。CPU {device['threads']} 线程，CUDA 可用为 {device['cuda_available']}，峰值显存 {device['peak_vram_bytes']} B。数据、IC 衰减和模型阶段 {device['wall_seconds']:.1f} s。Production / Demo 订单均为 0。

在模块目录运行

    .venv-cpu/Scripts/python.exe -B -m v2.reconcile
    powershell -NoProfile -ExecutionPolicy Bypass -File ./run_v2_windows.ps1 -Install -Phase portfolios
    powershell -NoProfile -ExecutionPolicy Bypass -File ./run_v2_windows.ps1 -Install -Phase research
    .venv-cpu/Scripts/python.exe -B -m v2.report
"""
    (out/"README.md").write_text(text,encoding="utf-8")
    summary=dict(reconciliation="PASS",new_factors=n,new_models=3,portfolio_variants=int(old.variant.nunique()),
                 best_validation=iw,ic_frozen=it.to_dict(),best_cost=cw,cost_frozen=ct.to_dict(),untouched=hold,
                 device=device,grade="BAR_PROXY",v1_max_ledger_error_usdt=float(prior.max_ledger_error_usdt.max()),
                 positive_historical_candidates=positive.to_dict("records"))
    (out/"summary.json").write_text(json.dumps(summary,indent=2,default=str))
    write_artifact_manifest(out,"crypto_alpha_v2")
    print(json.dumps(summary,default=str))


if __name__=="__main__":
    report(*setup())
