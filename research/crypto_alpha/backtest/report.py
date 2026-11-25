"""Portfolio and cost reports for an existing Nautilus execution adapter."""
import numpy as np
import pandas as pd

from strategy_framework.execution.backtest_report import _max_drawdown
from strategy_framework.paper_trading.core import daily_turnover_summary
from strategy_framework.paper_trading.stable_v22 import cost_capacity

from backtest.replay import replay
from portfolio.weights import weights


def portfolio(scores, panel, view, method, cfg, funding, funding_prices, economics=True):
    frame=panel.frame.loc[view["times"]]
    # Kept in configured symbol order by Panel's index.
    entry=pd.DatetimeIndex(frame.entry_time.to_numpy().reshape(scores.shape)[:,0])
    terminal_clock = "execution_end_time" if "execution_end_time" in frame else "label_end_time"
    exits=pd.DatetimeIndex(frame[terminal_clock].to_numpy().reshape(scores.shape)[:,0])
    time=entry.append(exits[-1:])
    proxy=np.vstack([panel.matrix("execution_price_proxy")[view["selector"]],
                     panel.matrix("terminal_execution_price_proxy")[view["selector"]][-1]])
    position_info=[weights(row,method,cfg["gross"],cfg["max_weight"],cfg["min_assets"]) for row in scores]
    relative, targets=map(np.stack,zip(*position_info))
    price=pd.DataFrame(proxy,index=time,columns=panel.symbols)
    targets_frame=pd.DataFrame(np.vstack([targets,np.zeros(len(panel.symbols))]),index=time,columns=panel.symbols)
    beta_column=(panel.matrix("btc_beta")[view["selector"]] if "btc_beta" in panel.frame
                 else np.full(scores.shape,np.nan))
    beta=pd.DataFrame(np.vstack([beta_column,beta_column[-1]]),index=time,columns=panel.symbols)
    active_funding={s:[e for e in rr if time[0].value<=e.event_time_ns<=time[-1].value]
                    for s,rr in funding.items()}
    capital=cfg["capital"]
    fees, spread, slip=(cfg[k] for k in ("fee_bps","spread_bps","slippage_bps"))
    trade=replay(price,targets_frame,active_funding,capital,fees,spread,slip,funding_prices,beta)
    daily=trade.groupby(trade.index.normalize()).agg(
        turnover_usdt=("turnover_usdt","sum"),turnover_raw=("turnover_equity","sum"),equity=("equity","last"))
    turn=daily_turnover_summary(daily.turnover_raw,complete_start=str(view["times"][0].date()),
                              complete_end_exclusive=str((view["times"][-1].normalize()+pd.Timedelta(days=1)).date()))
    daily_change=np.diff(np.r_[capital,daily.equity.to_numpy()])/np.r_[capital,daily.equity.to_numpy()[:-1]]
    sharpe=daily_change.mean()/daily_change.std(ddof=1)*np.sqrt(365) if len(daily_change)>1 and daily_change.std(ddof=1)>0 else np.nan
    p=trade[["gross_pnl","fees","spread","slippage","funding","turnover_usdt"]].sum()
    cap=cost_capacity(p.gross_pnl,p.funding,p.turnover_usdt,capital,trade.equity.iloc[-1]-capital)
    trade["drawdown"]=trade.equity / np.maximum.accumulate(np.maximum(trade.equity, capital))-1
    summary=dict(gross_return=p.gross_pnl/capital,net_return=trade.equity.iloc[-1]/capital-1,
                 max_dd=_max_drawdown([capital,*trade.equity]), daily_turnover=turn["mean_daily_turnover_raw"],
                 total_turnover=turn["total_turnover_raw"],turnover_usdt=p.turnover_usdt,sharpe=sharpe,
                 be_bps=cap["signed_BE_bps"],fee_only_be_bps=(p.gross_pnl+p.funding-p.spread-p.slippage)/p.turnover_usdt*1e4 if p.turnover_usdt else np.nan,
                 incremental_signed_be_bps=(trade.equity.iloc[-1]-capital)/p.turnover_usdt*1e4 if p.turnover_usdt else np.nan,
                 additional_cost_capacity_bps=cap["max_additional_cost_bps"],funding_pnl=p.funding,
                 fees=p.fees,spread=p.spread,slippage=p.slippage,beta_mean=trade.btc_beta.mean(),
                 beta_abs_mean=trade.btc_beta.abs().mean(),skipped_rebalances=int(trade.skipped.sum()),
                 funding_mark_proxy_count=int(trade.funding_mark_proxy_count.sum()),
                 unsettled_funding=int(trade.unsettled_funding.sum()),
                 terminal_gross_exposure=trade.gross_exposure.iloc[-1],
                 long_exposure_mean=trade.long_exposure.mean(),short_exposure_mean=trade.short_exposure.mean(),
                 net_exposure_mean=trade.net_exposure.mean(),execution_type="BAR_EXECUTION_PROXY")
    sensitivity=[]
    if economics:
        zero=replay(price,targets_frame,active_funding,capital,0,0,0,funding_prices)
        simple=replay(price,targets_frame,active_funding,capital,fees,spread,slip,funding_prices,fixed_capital=True)
        trade["zero_friction_equity"]=zero.equity
        trade["simple_1x_cumulative_return"]=simple.equity/capital-1
        summary["zero_friction_return"]=zero.equity.iloc[-1]/capital-1
        summary["simple_1x_cumulative_return"]=simple.equity.iloc[-1]/capital-1
        # Fixed-fill sensitivity uses actual traded notional and freezes the observed trading path.
        for bps in (0,1,2,4,6,8,10):
            for basis, gross in (("single_side_all_in",p.gross_pnl),
                                 ("fee_only_existing_spread_and_slippage",p.gross_pnl-p.spread-p.slippage)):
                sensitivity.append(dict(cost_bps=bps,basis=basis,filled_notional=p.turnover_usdt,
                                        net_return=(gross+p.funding-p.turnover_usdt*bps/1e4)/capital))
    return summary, trade, daily, pd.DataFrame(sensitivity), relative, targets
