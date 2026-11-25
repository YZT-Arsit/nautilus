"""Standard score-to-weight mapping and the preserved Nautilus execution engine."""
import numpy as np
import pandas as pd

from backtest.report import portfolio
from portfolio.interface import validate_export


def replay(scores, panel, view, cfg, funding, proxies, model_id):
    summary, ledger, _, _, strength, targets = portfolio(
        scores, panel, view, cfg["portfolio"], cfg, funding, proxies, economics=False)
    np.testing.assert_allclose(targets.sum(axis=1), 0, atol=1e-12)
    gross = np.abs(targets).sum(axis=1)
    if (gross > cfg["gross"] + 1e-12).any():
        raise ValueError("target gross exceeds limit")
    if cfg["portfolio"] == "rank" and cfg["max_weight"] >= cfg["gross"]/2:
        np.testing.assert_allclose(gross[gross > 0], cfg["gross"], atol=1e-12)
    total = ledger[["gross_pnl", "funding", "fees", "spread", "slippage"]].sum()
    error = float(ledger.equity.iloc[-1] - cfg["capital"] - (total.gross_pnl + total.funding -
                  total.fees - total.spread - total.slippage))
    if abs(error) > 1e-6 or summary["unsettled_funding"] or summary["terminal_gross_exposure"] > 1e-10:
        raise ValueError(f"ledger failed acceptance {error}, {summary}")
    rows = pd.MultiIndex.from_product([view["times"], panel.symbols],
                                      names=["prediction_time","symbol"]).to_frame(index=False)
    rows["execution_time"] = rows.prediction_time + pd.Timedelta(minutes=cfg["delay_minutes"])
    rows["model_version"] = model_id
    rows["prediction_score"] = scores.ravel()
    rows["relative_strength"] = strength.ravel()
    rows["target_weight"] = targets.ravel()
    export = validate_export(rows, cfg["delay_minutes"])
    return dict(**summary, ledger_error_usdt=error, min_target_gross=float(gross.min()),
                max_target_gross=float(gross.max())), ledger, rows, export
