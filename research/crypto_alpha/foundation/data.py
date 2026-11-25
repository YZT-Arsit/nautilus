"""Read-only, batch-by-symbol StandardBar and funding adapter."""
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Protocol

import numpy as np
import pandas as pd

from data.market import marks_archive, read_bars, read_funding
from data_engine.events import FundingRateEvent


@dataclass
class MarketSlice:
    symbol: str
    bars: pd.DataFrame
    funding: list[FundingRateEvent]
    coverage: dict
    inventory: list

    def records(self):
        """Unified minute records. Funding is populated only on the settlement minute."""
        result = self.bars.rename_axis("timestamp").reset_index().assign(symbol=self.symbol)
        funding = pd.Series([e.funding_rate for e in self.funding], index=pd.DatetimeIndex([
            pd.Timestamp(e.event_time_ns, unit="ns", tz="UTC").ceil("min") for e in self.funding]), dtype=float)
        if funding.index.has_duplicates:
            raise ValueError("multiple settlements in one minute")
        result["funding"] = result.timestamp.map(funding) if len(funding) else np.nan
        return result


class MarketSource(Protocol):
    def read(self, symbol: str, start: pd.Timestamp, end: pd.Timestamp,
             formal_start: pd.Timestamp) -> MarketSlice: ...


class NautilusMarket:
    def __init__(self, cfg, root: Path):
        self.primary = (root / cfg["data_root"]).resolve()
        self.extra = (root / cfg["supplemental_bar_root"]).resolve()
        self.marks = marks_archive([root.parents[1] / p for p in cfg["funding_mark_sources"]])

    def read(self, symbol, start, end, formal_start):
        bars, coverage, inventory = read_bars(self.primary, symbol, start, end, formal_start, self.extra)
        funding, duplicates = read_funding(self.primary, symbol, start, end, self.marks)
        formal = [e for e in funding if formal_start.value <= e.event_time_ns < end.value]
        coverage.update(funding_events=len(formal), funding_duplicates=duplicates,
                        official_funding_marks=sum(e.mark_price is not None for e in formal))
        return MarketSlice(symbol, bars, funding, coverage, inventory)


def funding_artifacts(batch):
    settlements = pd.DataFrame([asdict(e) for e in batch.funding])
    lookups = {}
    if not settlements.empty:
        stamps = pd.to_datetime(settlements.event_time_ns, utc=True).dt.floor("min")
        values = batch.bars.close.reindex(stamps).to_numpy()
        settlements["symbol"] = batch.symbol
        settlements["bar_mark_proxy"] = values
        lookups = {(e.event_time_ns, batch.symbol): v for e, v in zip(batch.funding, values)}
    return settlements, lookups
