"""Common historical feature grid, outcome clocks and mature supervision."""
import numpy as np
import pandas as pd

from foundation.data import funding_artifacts
from labels.alignment import align, normalize
from training.protocol import Panel


def build(source, cfg, registry):
    start, end = (pd.Timestamp(cfg[k], tz="UTC") for k in ("start", "end"))
    warm = start - pd.Timedelta(days=cfg["warmup_days"])
    grid = pd.date_range(warm, end, freq="30min", inclusive="left")
    clock = grid[grid >= start]
    horizons = sorted(set(cfg["evaluation_horizons_minutes"]) | {cfg["holding_minutes"], cfg["interval_minutes"]})
    features, inventory, coverage, events, proxies, settlements = [], [], [], {}, {}, []
    for symbol in cfg["symbols"]:
        print(f"DATA {symbol}", flush=True)
        batch = source.read(symbol, warm, end, start)
        bars = batch.bars
        factors, rows = registry.symbol(bars, grid, batch.funding)
        factors["prediction_time"] = grid
        factors["symbol"] = symbol
        factors["eligible"] = bars.close.reindex(grid).notna().to_numpy()
        factors["feature_available_time"] = grid.where(factors.eligible)
        opens = bars.open.copy()
        opens.index -= pd.Timedelta(minutes=1)
        price = {}
        for h in horizons:
            label = align(clock, bars.close, cfg["delay_minutes"], h)
            raw = label.label_raw.where(label.label_available_time <= end)
            price[f"return_{h}"] = raw
            price[f"close_end_{h}"] = label.label_exit_price.where(label.label_available_time <= end)
            price[f"open_end_{h}"] = opens.reindex(label.label_end_time).to_numpy()
        price["label_entry_price"] = bars.close.reindex(label.entry_time).to_numpy()
        price["execution_price_proxy"] = opens.reindex(label.entry_time).to_numpy()
        features.append(factors.join(pd.DataFrame(price, index=clock)).reset_index(drop=True))
        events[symbol] = batch.funding
        f, mark = funding_artifacts(batch)
        settlements.append(f)
        proxies.update(mark)
        coverage.append(batch.coverage)
        inventory.extend((symbol, *item) for item in batch.inventory)
    frame = pd.concat(features, ignore_index=True).sort_values(["prediction_time", "symbol"]).reset_index(drop=True)
    reg = registry.section(frame, rows)
    return frame.loc[frame.prediction_time >= start].reset_index(drop=True), reg, events, proxies, coverage, inventory, pd.concat(settlements)


def task(frame, interval, holding, delay, min_assets, epsilon):
    time = frame.prediction_time
    selected = frame.loc[(time.dt.hour * 60 + time.dt.minute) % interval == 0].copy()
    selected["entry_time"] = selected.prediction_time + pd.Timedelta(minutes=delay)
    selected["label_end_time"] = selected.entry_time + pd.Timedelta(minutes=holding)
    selected["label_available_time"] = selected.label_end_time
    selected["execution_end_time"] = selected.entry_time + pd.Timedelta(minutes=interval)
    selected["terminal_execution_price_proxy"] = selected[f"open_end_{interval}"]
    selected["label_exit_price"] = selected[f"close_end_{holding}"]
    selected["label_raw"] = selected[f"return_{holding}"].where(selected.eligible)
    return normalize(selected, min_assets, epsilon)


class CrossSectionDataset(Panel):
    def __init__(self, frame, symbols, factors, minimum):
        key_counts = frame.groupby("prediction_time").symbol.nunique()
        if not key_counts.eq(len(symbols)).all() or frame.duplicated(["prediction_time", "symbol"]).any():
            raise ValueError("every time section must include explicit rows for the configured universe")
        super().__init__(frame, symbols, factors, minimum)
        if not (frame.loc[frame.eligible, "feature_available_time"] <=
                frame.loc[frame.eligible, "prediction_time"]).all():
            raise ValueError("features are not available at the prediction time")

    def view(self, start, end, mature=True):
        result = super().view(start, end, mature)
        # A section remains unavailable until every ex-ante eligible asset's
        # label schedule has matured, even when a future price is missing.
        if mature:
            pending = result["eligible"] & ((result["available"] > end.value) |
                                            (result["available"] == np.iinfo(np.int64).min))
            incomplete = pending.any(axis=1)
            result["raw"][incomplete] = np.nan
            for label in result["labels"].values():
                label[incomplete] = np.nan
        else:
            schedule = self.frame["execution_end_time"].unstack("symbol").iloc[:, 0]
            fits = (schedule <= end).to_numpy()
            accepted = fits[result["selector"]]
            result = {k: v[accepted] if k not in {"selector", "labels"} else v
                      for k, v in result.items()}
            result["selector"] &= fits
            result["labels"] = {k: v[accepted] for k, v in result["labels"].items()}
        return result
