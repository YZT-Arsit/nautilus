"""Evaluate the sealed candidate list once on fresh nine-asset observations."""
import json
import shutil
from pathlib import Path
import warnings

import joblib
import lightgbm
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import dates as mdates
import numpy as np
import pandas as pd
import torch
from threadpoolctl import threadpool_limits

from v3.common import ROOT,digest,inputs,utc,verify_prior,write_json
from v3.features import fresh_panel
from v3.objectives import construct_v3,diagnostics,score_view,target,transform
from v3.portfolio import full_cost,variants
from v3.research import inputs_for
from models.cpu import net,torch_prediction
from v2.networks import construct
from strategy_framework.execution.backtest_report import write_artifact_manifest


def commit_eval(cfg,v1,v2,out):
    lock_path=out/"fresh_frozen_lock.json"
    lock=json.loads(lock_path.read_text())
    if lock["config_hash"]!=digest(ROOT/"configs/v3.json") or lock["shared_config_hash"]!=digest(ROOT/"configs/v2.json"):
        raise ValueError("configuration changed after freeze")
    if verify_prior(v1,v2)!=lock["frozen_results"]:
        raise ValueError("previous results changed after V3 freeze")
    for row in lock["cases"]:
        if [digest(p) for p in row["checkpoint"]]!=row["sha256"]:
            raise ValueError("checkpoint changed after freeze")
    selected=[r for r in variants(cfg) if r["name"] in {"v1_rank","partial_025","calibrated_return","no_trade_region"}]
    entries=[]
    for row in lock["cases"]:
        if row["model_key"].startswith("V2_"):
            policy=[row["rule"]]
        else:
            policy=[row["rule"],*[r for r in selected if r["name"]!=row["rule"]["name"]]]
        entries.append(dict(**row,policies=policy))
    plan=dict(lock_sha256=digest(lock_path),entries=entries,dates=[lock["fresh_start"],lock["fresh_end_exclusive"]],
              coverage_rule=lock["coverage_rule"],delay_minutes=cfg["delay_minutes"],risk_buffer_bps=cfg["risk_buffer_bps"],
              costs=lock["costs"],stage="before opening fresh research metrics")
    stored=out/"fresh_evaluation_plan.json"
    if stored.is_file() and json.loads(stored.read_text())!=plan:
        raise ValueError("the fresh plan already exists with different values")
    if not stored.is_file():
        write_json(stored,plan)
    return lock,entries


def infer(case,panel,view):
    f=case["model"]["family"]
    columns=[panel.factors.index(name) for name in case["features"]]
    path=Path(case["checkpoint"][0])
    prep=joblib.load(case["checkpoint"][1])
    if f=="MLP-MSE":
        model=net(len(columns))
        model.load_state_dict(torch.load(path,map_location="cpu",weights_only=True))
        x=prep.transform(view["x"][:,:,columns])
        return np.where(view["eligible"],torch_prediction(model,x),np.nan)
    if f=="LightGBM":
        fitted=lightgbm.Booster(model_file=str(path))
    elif f=="Ridge":
        fitted=joblib.load(path)
    else:
        fitted=(construct(f,len(columns),len(panel.symbols),8) if case["model_key"].startswith("V2_")
                else construct_v3(f,len(columns),len(panel.symbols)))
        fitted.load_state_dict(torch.load(path,map_location="cpu",weights_only=True))
    return score_view(fitted,f,transform(prep,panel,columns,f),view)


def run_fresh(cfg,v1,v2,out):
    lock,cases=commit_eval(cfg,v1,v2,out)
    print(f"FRESH PLAN SEALED {digest(out/'fresh_evaluation_plan.json')}",flush=True)
    data,registry,fund,first,last=fresh_panel(cfg,v2,out)
    registry.to_parquet(out/"fresh_feature_registry.parquet",index=False)
    rows=[]
    forecasts=[]
    trades=[]
    equity=[]
    sensitivity=[]
    monthly=[]
    for case in cases:
        horizon=case["model"]["horizon"]
        source,panel=inputs_for(data,registry.factor_name.tolist(),horizon,cfg)
        val=panel.view(first,last,mature=False)
        # Both bars must be real for the mandatory last close. Deterministic
        # truncation checks price availability only, without looking at outcomes.
        while len(val["times"]) and not np.isfinite(panel.matrix("terminal_execution_price_proxy")[val["selector"]][-1]).all():
            last_candidate=val["times"][-1]+pd.Timedelta(minutes=cfg["delay_minutes"])
            val=panel.view(first,last_candidate,mature=False)
        if not len(val["times"]):
            raise ValueError("fresh execution grid is empty")
        assert (val["available"][np.isfinite(val["raw"])]<=last.value).all()
        scores=infer(case,panel,val)
        post=target(val,panel.factors,case["model"]["label"])
        measures,ic,rank=diagnostics(scores,val,post)
        name=case["model_key"]
        t=val["times"]
        label_clock=t+pd.Timedelta(minutes=horizon+cfg["delay_minutes"])
        forecasts.append(pd.DataFrame(dict(prediction_time=np.repeat(t,len(panel.symbols)),
                          feature_available_time=source.set_index(["prediction_time","symbol"]).max_feature_available_time.reindex(
                              pd.MultiIndex.from_product([t,panel.symbols],names=["prediction_time","symbol"])).to_numpy(),
                          planned_execution_time=np.repeat(t+pd.Timedelta(minutes=cfg["delay_minutes"]),len(panel.symbols)),
                          label_available_time=np.repeat(label_clock,len(panel.symbols)),
                          symbol=panel.symbols*len(t),model=name,score=scores.ravel(),raw_label=val["raw"].ravel())))
        monthly.append(pd.DataFrame(dict(prediction_time=t,ic=ic,rank_ic=rank)).assign(
            month=t.strftime("%Y-%m")).groupby("month").agg(ic=("ic","mean"),rank_ic=("rank_ic","mean"),
            sections=("ic","count")).reset_index().assign(model=name,stage="fresh"))
        for spec in case["policies"]:
            p,ledger,weights,grid=full_cost(source,panel,val,scores,spec,case["slope"],fund,cfg)
            np.testing.assert_allclose(ledger.net_pnl.sum(),ledger.equity.iloc[-1]-cfg["capital"],atol=1e-7)
            if not (weights.target_weight.abs()<=cfg["max_weight"]+1e-12).all():
                raise ValueError("target cap violated")
            tag=dict(stage="fresh",model=name,variant=spec["name"],horizon_minutes=horizon,
                     rebalance_minutes=spec.get("rebalance_minutes",horizon))
            rows.append(dict(**tag,status="EVALUATED_FROZEN",universe=len(cfg["symbols"]),annual_cutoff=case["annual_cutoff"],
                             choice=case["rule"]["name"]==spec["name"],period_start=first,period_end_exclusive=last,
                             first_prediction=t[0],last_prediction=t[-1],first_execution=ledger.execution_time.iloc[0],
                             last_execution=ledger.execution_time.iloc[-1],**measures,**p))
            trades.append(weights.assign(**tag))
            equity.append(ledger.assign(**tag))
            sensitivity.append(pd.DataFrame(grid).assign(**tag))
        print(f"FRESH {name} IC {measures['ic']:.6f}",flush=True)
    result=pd.DataFrame(rows)
    result.to_csv(out/"fresh_test_results.csv",index=False)
    pd.concat(forecasts,ignore_index=True).to_parquet(out/"fresh_predictions.parquet",index=False,compression="zstd")
    pd.concat(trades,ignore_index=True).to_parquet(out/"fresh_target_weights.parquet",index=False,compression="zstd")
    pd.concat(equity,ignore_index=True).to_parquet(out/"fresh_ledger.parquet",index=False,compression="zstd")
    pd.concat(monthly).to_csv(out/"fresh_monthly_ic.csv",index=False)
    hist=pd.read_csv(out/"historical_cost_sensitivity.csv")
    pd.concat([hist,*sensitivity],ignore_index=True).to_csv(out/"cost_sensitivity.csv",index=False)
    write_json(out/"fresh_evaluation_receipt.json",dict(plan=digest(out/"fresh_evaluation_plan.json"),completed_utc=utc(),
                results_sha256=digest(out/"fresh_test_results.csv"),fresh_window=[str(first),str(last)],n_configs=len(result)))
    return result


def chart(out):
    fig,ax=plt.subplots(2,3,figsize=(16,8.7),constrained_layout=True)
    fig.suptitle("Crypto Alpha V3 | validation and sealed fresh evaluation")
    fq=pd.read_csv(out/"factor_quality.csv")
    a=fq.loc[fq.label_minutes.eq(240)].assign(abs_ic=lambda d:d.mean_ic.abs()).nlargest(10,"abs_ic")
    ax[0,0].barh(a.factor_name,a.mean_ic)
    ax[0,0].set_title("4h signed factor IC (research year)")
    f=pd.read_csv(out/"factor_ablation.csv")
    f=f.loc[f.subset.str.startswith("only:")]
    ax[0,1].barh(f.subset.str[5:],f.mean_ic)
    ax[0,1].set_title("Individual groups | 3-fold mean IC")
    v=pd.read_csv(out/"loss_comparison.csv")
    v=v.loc[v.family.eq("MLP")&v.horizon.eq(240)&v.label.eq("rank")]
    ax[0,2].bar(v.objective,v.mean_ic,yerr=v.sd_ic,alpha=.75)
    ax[0,2].set_title("4h MLP objectives | mean ± fold SD")
    p=pd.read_csv(out/"fresh_test_results.csv")
    d=pd.read_parquet(out/"fresh_ledger.parquet")
    for row in p.loc[p.choice].itertuples():
        x=d.loc[d.model.eq(row.model)&d.variant.eq(row.variant)]
        ax[1,0].plot(x.execution_time,x.equity/10000-1,label=row.model)
        daily=x.groupby(x.execution_time.dt.floor("D")).turnover_equity.sum()
        ax[1,1].plot(daily.index,daily,label=row.model)
    ax[1,0].legend(fontsize=7)
    ax[1,0].set_title("Fresh 6 bps equity return")
    ax[1,1].set_title("Fresh daily turnover / equity")
    for item in ax[1,:2]:
        item.xaxis.set_major_locator(mdates.WeekdayLocator(interval=2))
        item.xaxis.set_major_formatter(mdates.DateFormatter("%m-%d"))
        item.set_xlabel("Date (UTC)")
    c=pd.read_csv(out/"cost_sensitivity.csv")
    chosen=p.loc[p.choice&p.model.str.startswith("V2_").eq(False)].iloc[0]
    c=c.loc[c.stage.eq("fresh")&c.model.eq(chosen.model)&c.variant.eq(chosen.variant)]
    for k,x in c.groupby("mode"):
        ax[1,2].plot(x.bps,x.return_net,marker="o",label=k)
    ax[1,2].legend(fontsize=7)
    ax[1,2].set_title("V3 fresh total cost sensitivity")
    for item in ax.flat:
        item.grid(alpha=.2)
        item.tick_params(labelsize=8)
    fig.savefig(out/"summary.png",dpi=150)
    plt.close(fig)


def report(cfg,v1,v2,out,fresh):
    env=json.loads((out/"run_environment.json").read_text())
    select=json.loads((out/"selected_research.json").read_text())
    winner=select["validation_main"]
    selected=fresh.loc[fresh.choice&~fresh.model.str.startswith("V2_")].iloc[0]
    master=fresh.loc[fresh.model.eq("V2_MASTER-IC")].iloc[0]
    sample=pd.read_csv(out/"model_comparison.csv")
    best_hist=sample.loc[sample.ic.idxmax()]
    names=pd.read_csv(out/"universe_catalogue.csv")
    markets=int((names.history_partition_days>0).sum())
    comparison=pd.DataFrame([dict(size=9,available_historical_symbols=markets,status="TESTED",note="specified fixed baseline")]+[
        dict(size=n,available_historical_symbols=markets,status="BLOCKED_INSUFFICIENT_ARCHIVED_CONTRACTS",
             note="count of partitions in existing Nautilus USD-M 1m archive; no extended-universe model run") for n in (20,30,50)])
    comparison.to_csv(out/"universe_comparison.csv",index=False)
    folds=pd.read_csv(out/"validation_results.csv")
    ablation=pd.read_csv(out/"factor_ablation.csv")
    best_group=ablation.loc[ablation.subset.str.startswith("only:")].sort_values("mean_ic",ascending=False).iloc[0]
    best_drop=ablation.loc[ablation.subset.str.startswith("drop:")].sort_values("mean_ic",ascending=False).iloc[0]
    highest_validation=pd.read_csv(out/"loss_comparison.csv").nlargest(1,"mean_ic").iloc[0]
    rank=fresh.loc[fresh.model.eq(selected.model)&fresh.variant.eq("v1_rank")].iloc[0]
    profitable=fresh.loc[(fresh.net_return>0)&(fresh.trades>0),["model","variant","net_return","be_bps"]]
    old=pd.read_csv(v2/"portfolio_comparison.csv").assign(stage="V2_IMMUTABLE_BASELINE")
    revised=pd.read_csv(out/"portfolio_comparison.csv")
    revised=revised.loc[~revised.stage.isin(["fresh","V2_IMMUTABLE_BASELINE"])]
    pd.concat([revised,old,fresh],ignore_index=True).to_csv(out/"portfolio_comparison.csv",index=False)
    group_table="；".join(f"{r.model}/{r.variant} {r.net_return:.4%} (BE {r.be_bps:.3f} bps)" for r in profitable.itertuples()) or "无"
    g=folds.loc[folds.name.eq(selected.model)&folds.seed.eq(cfg["seed"])]
    group_sd=", ".join(f"{x:.5f}" for x in g.ic)
    summary=dict(new_factors=8,universe_sizes=[9],validation=winner,historical_ic_max=best_hist.to_dict(),
                 fresh_main=selected.to_dict(),master_fresh=master.to_dict(),
                 profitable=profitable.to_dict("records"),best_group=best_group.to_dict(),
                 best_group_removal=best_drop.to_dict(),environment=env,evidence="BAR_PROXY")
    write_json(out/"summary.json",summary)
    chart(out)
    (out/"README.md").write_text(f"""# CRYPTO ALPHA V3

## 验证与冻结

V1/V2 产物逐文件 SHA-256 核对。三版主 IC 均为按时间截面的原始收益 Pearson IC 的均值。V1/V2 的 4h 验证区间为 2025-04-01 至 07-01，V3 使用 2024-10、2025-01、2025-04 起的三个顺序窗口。训练端以标签可用时刻清除跨界样本，并留出 4 小时隔离。模型按均值 IC 减 0.2 倍窗口标准差选择，经济指标单列。建仓策略按后两个验证窗口 6 bps 净收益及至少 10 次成交选择。

验证首选为 {selected.model}，验证平均 IC {winner['mean_ic']:.6f}，折间 SD {winner['sd_ic']:.6f}，最差折 {winner['worst_ic']:.6f}，三折为 {group_sd}。V1 已观察年度只做历史对照，记录最高 IC {best_hist.ic:.6f}，模型 {best_hist['name']}。V3 主模型在 2026-07-01 使用前 12 个月完成年度重拟合，模型结构和组合参数沿用首年选择。校准以各年度内顺序样本外预测拟合斜率。V2 两个历史冻结模型按原权重、校准和建仓参数进入新样本。

未加折间波动惩罚时，最高验证均值为 {highest_validation['name']} 的 {highest_validation.mean_ic:.6f}，折间 SD {highest_validation.sd_ic:.6f}。

## 新数据

使用现有 Nautilus BTC、ETH、SOL 分区；对缺失日期补充六币的 Binance USD-M 1m K 线和必要官方 Funding mark。分钟可用性以已完成 Bar 为准。评估方案和权重哈希先于新数据收益评估锁定。共同有效窗口 {selected.period_start} 至 {selected.period_end_exclusive}（不含右端），以每天每币至少 1433 根有效分钟且 Funding 最大间隔不超过 12 小时确定。最后一次成交 {selected.last_execution}。缺失分区、有效 Bar 数和共同日均可查。

九币固定池已测试，原目录只有 {markets} 个历史合约。20/30/50 币实验缺少所需既有历史档案，本轮下载仅覆盖本请求的六币缺口。

## 因子和模型

保留 88 个原因子并新增 8 个，包括前序 Beta 对冲残差、剩余动量、反转、流动性和 Funding 交互。因子审计含六个预测期限、月度 IC、覆盖率、ICIR、相关性及 BTC 波动状态。组消融全部沿用相同 Ridge、标签和折窗。个体组最高为 {best_group.subset}，IC {best_group.mean_ic:.6f}；最佳单组删除为 {best_drop.subset}，IC {best_drop.mean_ic:.6f}。Premium/Basis/OI 本轮没有通过既有字段可用性验证。

六类模型对比用完整的横截面。IC 与 Huber 混合用拟合期原始收益波动率缩放回归部分。时间一致性仅在相邻时间片段生效。原始收益、排名 Z-score、横截面收益 Z-score、BTC Beta 残差四种目标分别记录对原始收益 IC、Rank IC，以及各自训练标签 IC。

## 新样本经济结果

|模型|策略|IC|Rank IC|价格毛收益|6 bps 净收益|日均换手|总 BE|
|---|---|---:|---:|---:|---:|---:|---:|
{chr(10).join(f'|{r.model}|{r.variant}|{r.ic:.6f}|{r.rank_ic:.6f}|{r.gross_return:.3%}|{r.net_return:.3%}|{r.daily_turnover:.3f}×|{r.be_bps:.3f} bps|' for r in fresh.itertuples())}

新样本盈利配置为 {group_table}。新样本结果不参与模型或组合选择。主配置为 {selected.variant}，预测、Label 和常规调仓间隔 {selected.horizon_minutes}/{selected.horizon_minutes}/{selected.rebalance_minutes} 分钟，执行延迟 10 分钟，实际平均持仓 {selected.mean_holding_hours:.2f} 小时。25% 分步调仓的实际持仓时间可长于 4h 标签，持仓周期按仓位从建立到归零或反向记录。

主配置的固定成交路径去成本 {selected.fixed_path_zero_return:.3%}，0 bps 独立动态重跑 {selected.zero_cost_rerun:.3%}，6 bps 固定本金简单收益 {selected.fixed_capital_simple_6bps:.3%}，6 bps 动态净收益 {selected.net_return:.3%}。手续费 4 bps、点差 1 bp、滑点 1 bp，资金费另计。0/1/2/4/6/8/10 bps 均区分固定路径和独立动态重跑。逐时实际仓位、目标权重、BTC beta 及长短敞口保留。执行口径 BAR_PROXY。

总 BE 以实际成交路径的价格盈亏加资金费作分子，以累计成交名义额作分母，并乘以 10,000。主模型的 EMA 将同一信号的日均换手由 {rank.daily_turnover:.3f}× 降至 {selected.daily_turnover:.3f}×。同时平均持仓从 {rank.mean_holding_hours:.2f} 小时增至 {selected.mean_holding_hours:.2f} 小时，超过训练标签的 1 小时区间。

## 复现

Windows CPU {env['threads']} 线程，CUDA {env['cuda_available']}，峰值 RAM {env['peak_ram_bytes']} B，显存 0 B。研究和年度训练阶段 {env['wall_seconds']:.1f} 秒。Production / Demo 订单均为 0。

    powershell -NoProfile -ExecutionPolicy Bypass -File ./run_v3_windows.ps1 -Install -Phase acquire
    powershell -NoProfile -ExecutionPolicy Bypass -File ./run_v3_windows.ps1 -Install -Phase research
    powershell -NoProfile -ExecutionPolicy Bypass -File ./run_v3_windows.ps1 -Install -Phase finalize
    .venv-cpu/Scripts/python.exe -B -m v3.verify
""",encoding="utf-8")
    return summary


if __name__=="__main__":
    cfg,v1,v2,out=inputs()
    warnings.filterwarnings("ignore",message="Mean of empty slice")
    warnings.filterwarnings("ignore",message="invalid value encountered in expm1")
    torch.set_num_threads(cfg["threads"])
    torch.set_num_interop_threads(1)
    if (out/"fresh_evaluation_receipt.json").exists():
        commit_eval(cfg,v1,v2,out)
        receipt=json.loads((out/"fresh_evaluation_receipt.json").read_text())
        if digest(out/"fresh_test_results.csv")!=receipt["results_sha256"]:
            raise ValueError("sealed fresh result changed")
        fresh=pd.read_csv(out/"fresh_test_results.csv")
    else:
        with threadpool_limits(limits=cfg["threads"]):
            fresh=run_fresh(cfg,v1,v2,out)
    report(cfg,v1,v2,out,fresh)
    print("V3 COMPLETE",flush=True)
    write_artifact_manifest(out,"crypto_alpha_v3")
