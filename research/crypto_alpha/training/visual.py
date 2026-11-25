"""Headless summary figure for each frozen test."""
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt


def summary_plot(out,interval,model,ic,pnl,turnover,cost,capital):
    fig,axes=plt.subplots(3,3,figsize=(16,11),constrained_layout=True)
    fig.suptitle(f"Crypto Alpha V1 | {interval}m frozen test | {model}",fontsize=18)
    x=ic[(ic.model==model)&(ic.split=="test")].set_index("prediction_time")
    p=pnl[(pnl.model==model)&(pnl.weight_method=="rank")].set_index("execution_time")
    t=turnover[(turnover.model==model)&(turnover.weight_method=="rank")].set_index("date")
    c=cost[(cost.model==model)&(cost.weight_method=="rank")]
    for ax,col,title in ((axes[0,0],"ic","Pearson IC"),(axes[0,1],"rank_ic","Spearman Rank IC")):
        x[col].plot(ax=ax,alpha=.25,lw=.5)
        x[col].rolling(96,min_periods=12).mean().plot(ax=ax,lw=1.4)
        ax.set_title(title)
    x[["ic","rank_ic"]].cumsum().plot(ax=axes[0,2],title="Cumulative IC")
    (p[["gross_long_pnl","gross_short_pnl"]].cumsum()/capital).plot(ax=axes[1,0],title="Long and short price PnL / initial USDT")
    (p[["zero_friction_equity","equity"]]/capital-1).plot(ax=axes[1,1],title="Zero friction and net return")
    p[["long_exposure","short_exposure","net_exposure","btc_beta"]].plot(ax=axes[1,2],title="Exposure and ex-ante BTC beta",alpha=.7)
    t.turnover_raw.plot(ax=axes[2,0],title="Daily turnover / account equity")
    p.drawdown.plot.area(ax=axes[2,1],title="Drawdown")
    c.pivot(index="cost_bps",columns="basis",values="net_return").plot(
        ax=axes[2,2],marker="o",title="Cost sensitivity (fixed fills)")
    for ax in axes.flat:
        ax.grid(alpha=.2)
        ax.tick_params(labelsize=8)
    fig.savefig(out/"summary.png",dpi=150)
    plt.close(fig)
