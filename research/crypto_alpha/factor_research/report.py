"""Report selection and diagnostics without mixing the historical test."""
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd

from factor_research.reproduction import compare


def plot(out,cfg,table,months,corr,means):
    h = cfg["main_horizon"]
    v = table.query("split=='INTERNAL_VALIDATION' and holding_minutes==@h")
    top = v.nlargest(10,"directed_ic").sort_values("directed_ic")
    names = top.factor.tolist()
    fig, ax = plt.subplots(2,3,figsize=(17,9),constrained_layout=True)
    ax[0,0].barh(names,top.directed_ic)
    ax[0,0].set_title("Validation | Pearson IC, signed by train")
    m = months.query("holding_minutes==@h and split!='OBSERVED_HISTORICAL_TEST' and factor in @names")
    heat = m.pivot(index="factor",columns="month",values="directed_ic").reindex(names)
    im = ax[0,1].imshow(heat,aspect="auto",cmap="RdBu_r",vmin=-.15,vmax=.15)
    ax[0,1].set_xticks(range(len(heat.columns)),heat.columns,rotation=60)
    ax[0,1].set_yticks(range(len(names)),names)
    ax[0,1].set_title("Monthly IC | research year")
    fig.colorbar(im,ax=ax[0,1],shrink=.8)
    for name in names[-5:]:
        curve = table.query("split=='INTERNAL_VALIDATION' and factor==@name").sort_values("holding_minutes")
        ax[0,2].plot(curve.holding_minutes/60,curve.directed_ic,label=name,marker=".")
    ax[0,2].set_xscale("log")
    ax[0,2].set_xlabel("Holding period (hours), 10 minute delay")
    ax[0,2].set_title("Validation IC decay")
    ax[0,2].legend(fontsize=7)
    fig.colorbar(ax[1,0].imshow(corr["spearman"],vmin=-1,vmax=1,cmap="RdBu_r"),ax=ax[1,0],shrink=.8)
    ax[1,0].set_title("Training cross-sectional Spearman correlation")
    q = pd.read_csv(out/"quantile_returns.csv").query("split=='INTERNAL_VALIDATION' and factor in @names")
    heat = q.pivot(index="factor",columns="group",values="mean_demeaned_return").reindex(index=names,columns=[1,2,3])*1e4
    im = ax[1,1].imshow(heat,aspect="auto",cmap="RdBu_r")
    ax[1,1].set_yticks(range(len(names)),names)
    ax[1,1].set_xticks([0,1,2],["Bottom","Middle","Top"])
    ax[1,1].set_title("Validation tercile mean return (bps)")
    fig.colorbar(im,ax=ax[1,1],shrink=.8)
    best = means.head(10).sort_values("mean_ic")
    ax[1,2].barh(best.index,best.mean_ic)
    ax[1,2].axvline(means.loc["full","mean_ic"],color="black",ls="--")
    ax[1,2].set_title("Ablation | three-model mean validation IC")
    for item in ax.flat:
        item.tick_params(labelsize=7)
    fig.savefig(out/"summary.png",dpi=135)
    plt.close(fig)


def deliver(out,cfg,reg,table,months,corr,clusters,means,lock,common_days,test):
    upstream = compare(out,reg)
    plot(out,cfg,table,months,corr,means)
    h = cfg["main_horizon"]
    counts = upstream.groupby(["project","status"]).size()
    groups = means.loc[means.index.str.startswith("only_")].head(3)
    horizons = pd.Series(lock["best_training_horizons"]).value_counts().to_dict()
    positive = int((table.query("holding_minutes==@h and split=='TRAIN'").mean_ic>0).sum())
    s = dict(total_factors=len(reg),documented=len(reg),factor_categories=reg.category.value_counts().to_dict(),
             positive_raw_training_ic=positive,stable_validation=int(clusters.stable.sum()),
             total_clusters=int(clusters.cluster.nunique()),
             redundant_clusters=int(clusters.loc[clusters.cluster_size.gt(1),"cluster"].nunique()),
             largest_cluster=int(clusters.cluster_size.max()),best_groups=groups.mean_ic.to_dict(),
             best_training_horizon_counts=horizons,selected=lock["chosen_set"],
             full_validation_ic=lock["validation_ic_full"],selected_validation_ic=lock["validation_ic_selected"],
             incremental_ic=lock["incremental_ic"],tests=test["passed"],fresh_common_bar_directories=common_days,
             qlib_numerical=int(counts["Qlib Alpha158","NUMERICALLY_REPRODUCED"]),
             veighna_numerical=int(counts["VeighNa Alpha158","NUMERICALLY_REPRODUCED"]),
             veighna_different=int(counts.get(("VeighNa Alpha158","FORMULA_REVIEWED"),0)),
             formula_text_duplicates=int(reg.identical_formula_text.sum()),constant_training=int(reg.constant.sum()))
    leader = "\n".join(f"- {g.removeprefix('only_')} 平均验证 IC {row.mean_ic:.5f}" for g,row in groups.iterrows())
    (out/"README.md").write_text(f"""# FACTOR RESEARCH V1

## 数据与冻结

只读复用 Foundation 的九币数据、精确分钟完成价、因子注册、标签、CPU 模型及 Nautilus 账本。核验 Foundation 的代码和 {lock['foundation']['artifacts']} 个产物。实际运行 {test['passed']} 项测试。

- TRAIN 为 2024-07-01 至 2025-04-01，INTERNAL VALIDATION 为 2025-04-01 至 2025-07-01。日期为 UTC，右端不含。标签必须在对应截止前成熟。
- OBSERVED HISTORICAL TEST 为 2025-07-01 至 2026-07-01。在 `selected_factor_sets.json` 锁定后才评价这一历史年度。历史组合采用 BAR_EXECUTION_PROXY。
- 2026-07-01 至 2026-08-26 已用于 V3。FRESH UNTOUCHED TEST 仅检查 {cfg['fresh_metadata_start']} 以后的 Nautilus 分区目录，共同 Bar 分区日期 {common_days} 个。目录证据用于覆盖排查。

## 因子与源码

研究 {s['total_factors']} 个分钟因子。使用 60 分钟预测时钟、10 分钟执行延迟和 30／60／120／240／480／1440 分钟的六套标签。两份精确完成价逐期限生成收益，缺价保持 NaN。因果性验收使用训练期真实分钟 Bar，扰动未来价格和 Funding 后核对输出。因子来源、数学公式、公式审查、缺值、异常及常数截面见目录。

Qlib 158 条日频表达式通过完整预热样本的数值核对，比基座增加 120 条。VeighNa 158 条日频表达式均执行源码，{s['veighna_numerical']} 条与日频对照一致，{s['veighna_different']} 条的数值定义存在差异。AlphaGen 单因子、互相关和线性组合 IC，以及 FreqAI 特征标签发现和时序权重均有数值证据。日频对照与分钟研究分别记录。Premium、Basis、OI 缺少经核验历史字段。

## 因子评价

IC 按时间截面计算，常数与资产不足截面保留缺失。ICIR 使用样本标准差。95% 区间按 {cfg['block_days']} 个 UTC 日的连续块重采样 {cfg['bootstrap_draws']} 次。比较原值、横截面排名、Z-score 和 TRAIN 0.5%／99.5% Winsor。异常用 3 IQR 衡量。1h 原始 TRAIN IC 为正的因子有 {positive} 个。

按 1h TRAIN IC 确定方向，再以 TRAIN 方向调整 IC 排期限；两段方向调整均值大于 0、每段至少三分之二月份为正且 TRAIN 覆盖不低于 60% 的稳定因子有 {s['stable_validation']} 个。训练期横截面绝对 Spearman 阈值 {cfg['cluster_abs_spearman']}、complete linkage，得到 {s['redundant_clusters']} 个多成员簇。主期限逐截面资产数含在 `factor_ic_by_section.parquet`。

按当时可见的因子和冻结方向分三组，重复断点时跳过。成熟结果仅用于分组收益。实际组人数、Top–Bottom、单调性比例、月度稳定性与成员换手均保留。

## 同预算组消融

Ridge、LightGBM、MLP-IC 共用 1h Rank Z-score 标签。MLP-IC 按完整横截面优化 Pearson IC。LightGBM 上限 {cfg['lightgbm_round_limit']} 轮，MLP 上限 {cfg['epochs']} epoch。以三模型内部验证 Pearson IC 的等权均值选集。

{leader}

全量 IC {s['full_validation_ic']:.5f}，首选集 `{s['selected']}` IC {s['selected_validation_ic']:.5f}，增量 {s['incremental_ic']:+.5f}。这些是参与选集的内部验证结果。

## 复现

Windows 研究目录运行全库或单因子任务。

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File ./run_factor_research_windows.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File ./run_factor_research_windows.ps1 -Factor ret_60m
```

上游日频对照使用 Qlib 0.9.7 与 Polars 1.35.2 环境，运行 `python -m factor_research.upstream_daily --output references/factor_research_parity`。主任务消费此对照文件。新增因子沿用 Foundation `Registry.register(Factor(...))`，在 Phase 2 配置填写 `factor_plugins`，随后自动完成计算、因果检查、IC、分位和组消融。

Production / Demo orders = 0 / 0
""",encoding="utf-8")
    return s
