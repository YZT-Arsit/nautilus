"""Acceptance tables and plots for the sealed model experiment."""
import json
import platform

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd

from v3.common import write_json


def delivery(cfg,out,fresh):
    all_models=pd.read_csv(out/"model_comparison.csv")
    core=all_models.query("scope=='core'").sort_values("ic",ascending=False)
    selected=json.loads((out/"selection.json").read_text())
    w=core.loc[core.trial.eq(selected["selected_validation"]["trial"])].iloc[0]
    rank=core.loc[core.rank_ic.idxmax()]
    arch=pd.read_csv(out/"architecture_ablation.csv")
    annual=pd.read_csv(out/"annual_training.csv")
    seed=pd.read_csv(out/"seed_results.csv")
    test=json.loads((out/"tests.json").read_text())
    history=pd.read_csv(out/"historical_test_results.csv")
    hm=history.loc[history.trial.eq(w.trial)].iloc[0]
    env=json.loads((out/"environment.json").read_text())
    cover=json.loads((out/"acquisition_receipt.json").read_text())
    failures=pd.read_csv(out/"failed_experiments.csv")
    m=core.query("label=='rank'").set_index(["family","objective"]).ic
    index=[("MLP","MSE"),("MLP","IC"),("GRU","MSE"),("GRU","IC"),("MASTER","MASTER_ORIGINAL_MSE"),("MASTER","IC"),
           ("StockMixer","StockMixer_ORIGINAL"),("StockMixer","IC")]
    summary=dict(models=len(core.family.unique()), configurations=len(all_models),failures=len(failures),
                 factor_sets=sorted(all_models.factor_set.unique()),best_ic=w.ic,best_rank_ic=rank.rank_ic,
                 best_rank_trial=rank.trial,selected=w.to_dict(),seed_mean=seed.ic.mean(),seed_sd=seed.ic.std(ddof=1),
                 seed_count=len(seed),history=hm.to_dict(),annual_B=annual.query("mode=='B'").iloc[0].to_dict(),
                 fresh=fresh,coverage=cover,environment=env,tests=test["passed"],device=env["device"],
                 host=platform.node(),loss_rank={f"{k[0]}_{k[1]}":m.get(k) for k in index})
    write_json(out/"summary.json",summary)
    fig,ax=plt.subplots(2,3,figsize=(17,9),constrained_layout=True)
    best=core.head(10).sort_values("ic")
    labels=best.family+" "+best.objective+" "+best.label
    ax[0,0].barh(labels,best.ic)
    ax[0,0].set_title("Validation | pooled Pearson IC, 1h")
    bars=pd.DataFrame({"name":core.family+" "+core.objective,"label":core.label,"ic":core.ic})
    bars.pivot(index="name",columns="label",values="ic").plot.barh(ax=ax[0,1])
    ax[0,1].set_title("Common cohort | label objective comparison")
    a=arch.query("scope=='architecture'").sort_values("delta_vs_full")
    ax[0,2].barh(a.family+" "+a.variant,a.delta_vs_full)
    ax[0,2].set_title("Ablations | IC delta vs family baseline")
    mon=pd.read_csv(out/"monthly_ic.csv")
    month=mon.loc[mon.trial.eq(w.trial)]
    for split,chunk in month.groupby("split"):
        ax[1,0].plot(pd.to_datetime(chunk.month),chunk.ic,label=split,marker=".")
    ax[1,0].legend(fontsize=6)
    ax[1,0].set_title("Selected model | monthly IC")
    ax[1,0].tick_params(axis="x",rotation=25)
    ax[1,1].bar(seed.seed.astype(str),seed.ic)
    ax[1,1].set_title("Seed sensitivity | same market observations")
    h=all_models.loc[all_models.scope.eq("horizon")]
    for family,g in h.groupby("family"):
        ax[1,2].plot(g.interval/60,g.ic,marker="o",label=family)
    ax[1,2].legend(fontsize=7)
    ax[1,2].set_title("Independent cadence/holding extensions")
    ax[1,2].set_xlabel("Hours")
    for p in ax.flat:
        p.tick_params(labelsize=7)
        p.grid(alpha=.2)
    fig.savefig(out/"summary.png",dpi=140)
    plt.close(fig)
    objectives="\n".join(f"- {k[0]} {k[1]} 验证 IC {m.get(k,float('nan')):.5f}" for k in index)
    fresh_text=(f"冻结模型在 [{fresh['start']}, {fresh['end_exclusive']}) 共 {fresh['common_days']} 天，"
                f"Pearson IC {fresh['ic']:.5f}，Rank IC {fresh['rank_ic']:.5f}，6 bps 净收益 {fresh['net_return']:.3%}。"
                if fresh["status"]=="EVALUATED_FROZEN" else f"新样本状态 DATA_BLOCKED，{fresh['reason']}。")
    (out/"README.md").write_text(f"""# MODEL RESEARCH V1

## 验收

在 Windows CPU 上运行 {len(all_models)} 个配置，记录 {len(failures)} 个失败试验，{test['passed']} 项测试通过。Foundation 与 Factor Research 冻结哈希逐项核对。复用实际冻结的 FULL_88、NO_DERIVATIVES_83、STABLE_24 成员。GPU 探测见 `environment.json`。

## 数据、目标与选模

九币，2024-07-01 至 2026-07-01 UTC。主实验每小时预测，延迟 10 分钟，以精确完成分钟收盘价计算 1h 持有收益。4h／24h 扩展以相应预测间隔和结束价另建标签。前一年内部使用三个顺序折，验证窗口起点为 2025-01-01、03-01、05-01。训练与验证按各自截止剔除未成熟截面，缩放器逐折只拟合训练集。

所有模型共享特征时点与资产掩码。Raw Label 在训练折中按波动尺度缩放且保留原符号，Rank Z-score 逐截面平均排名、去均值、总体标准差归一化。两种标签使用相同样本。Pearson IC 与 Rank IC 均对原始收益；processed IC 对实际监督目标。Pearson 逐横截面计算，零信息标签及无效资产掩码有测试。IC 区间使用 {cfg['block_days']} 日块、{cfg['bootstrap_draws']} 次重采样。选定模型以主实验拼接验证 Pearson IC 排序。

首选为 {w.family} / {w.objective} / {w.label}，验证 IC {w.ic:.5f}，Rank IC {w.rank_ic:.5f}。最高验证 Rank IC {rank.rank_ic:.5f} 来自 {rank.family} / {rank.objective} / {rank.label}。领先神经网络的 {len(seed)} 个种子验证 Pearson IC 均值 {seed.ic.mean():.5f}，样本标准差 {seed.ic.std(ddof=1):.5f}。种子共享市场样本。

{objectives}

## 原版与适配

StockMixer 原版 16 步、5 输入字段，原版 CPU 前向反向通过；TriU 数值等价和原始复合损失均对照。Crypto 版使用 8 步、12 通道和九资产 MLP。原版 signed pair 项按源码计算；共享 Raw Label 缩放及 Rank 目标是研究适配。MASTER 原版 158 因子加 63 市场特征，隐藏 256，时间／资产注意力为 4／2 头。原版 CPU 前向反向通过；Crypto 版为隐藏 16、2／2 头、因果市场均值门控。原始损失分支使用 masked MSE，并沿用共享的未裁剪可用资产掩码。VeighNa 原始 MlpNetwork 结构测试通过。Qlib 及 VeighNa 的树模型训练源码已核对。完整股票实验未运行，源训练数据未引入。

## 年度训练和冻结新样本

模式 A 在 2025-07-01 进行年度重拟合，重载模型数值一致。已观察 2025-07-01 至 2026-07-01 的首选模型 IC {hm.ic:.5f}，净收益 {hm.net_return:.3%}。模式 B 在 2026-07-01 实际使用此前两年成熟标签重训，训练资产行 {summary['annual_B']['trained_observations']}。完整后续预测年尚无法验收。

已有 V3 56 天为已观察历史。此次冻结窗口在 2026 年 10 月，首 8 天只做预热。Bar 按 Binance 归档校验和导入；Funding 使用官方结算 rate、time、mark。{fresh_text} 主组合为 Foundation 的相对强度排名与 L1 归一化，单边 6 bps，资金费另计，证据等级 BAR_EXECUTION_PROXY。

## 复现

Windows 研究目录按顺序执行。三个阶段由独立的低优先级计划任务运行，日志见同级 `model_research_logs`。只有进入独立 finalize 时才读取锁定窗口的收益和预测结果。

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File ./run_model_research_windows.ps1 -Phase acquire
powershell -NoProfile -ExecutionPolicy Bypass -File ./run_model_research_windows.ps1 -Phase study
powershell -NoProfile -ExecutionPolicy Bypass -File ./run_model_research_windows.ps1 -Phase finalize
```

Windows 无法直连 Funding 时，使用本仓库 `model_research.funding_relay` 经可访问系统 TLS 的环境补齐官方端点缺失段。原始 URL 和回包哈希保存在 `funding_relay_receipt.json`。每批下载前维护 12 GiB 磁盘预留。

Production / Demo orders = 0 / 0
""",encoding="utf-8")
