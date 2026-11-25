"""Read-only bridge from Nautilus StandardBar/Hive and funding events."""
from dataclasses import replace
from hashlib import sha256
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow.dataset as ds

from data_engine.sources.hive_partitioning import matching_fragments, select_date_window_fragments
from data_engine.sources.parquet_funding import ParquetFundingSource
from labels.alignment import bar_available_index

FILTERS = {"asset_class": "crypto", "exchange": "BINANCE", "venue_type": "futures_um"}
CORE = ["open", "high", "low", "close", "volume"]
EXTRA = ["quote_volume", "trade_count", "taker_buy_volume", "taker_buy_quote_volume"]


def symbol_dir(root, symbol):
    return root.joinpath(*(f"{k}={v}" for k, v in FILTERS.items()), f"symbol={symbol}")


def read_bars(root, symbol, start, end, formal_start, supplemental=None):
    path = symbol_dir(root, symbol) / "data_type=bar" / "freq=1m"
    dataset = ds.dataset(str(path), format="parquet", partitioning="hive")
    filters = {**FILTERS, "symbol": symbol, "data_type": "bar", "freq": "1m"}
    all_fragments = matching_fragments(dataset, filters)
    fragments = select_date_window_fragments(all_fragments, start.date(), end.date())
    partition_dates = {Path(f.path).parent.name for f in all_fragments}
    sources=[(root,f) for f in fragments]
    if supplemental is not None:
        extra=symbol_dir(supplemental,symbol)/"data_type=bar"/"freq=1m"
        if extra.is_dir():
            extra_ds=ds.dataset(str(extra),format="parquet",partitioning="hive")
            missing=[f for f in matching_fragments(extra_ds,filters) if Path(f.path).parent.name not in partition_dates]
            sources.extend((supplemental,f) for f in select_date_window_fragments(missing,start.date(),end.date()))
    blocks = []
    manifest = []
    for catalogue, fragment in sorted(sources, key=lambda pair: pair[1].path):
        fields = set(fragment.physical_schema.names)
        required = {"ts", *CORE}
        if not required <= fields:
            raise ValueError(f"{fragment.path}: required columns missing {required - fields}")
        present = [c for c in [*EXTRA, "source", "is_trade_bar"] if c in fields]
        table = fragment.to_table(columns=["ts", *CORE, *present], use_threads=False)
        blocks.append(table.to_pandas())
        file = Path(fragment.path)
        manifest.append(("Nautilus primary" if catalogue==root else "Nautilus importer supplemental",
                         file.relative_to(catalogue).as_posix(),table.num_rows,file.stat().st_size))
    frame = pd.concat(blocks, ignore_index=True)
    times = pd.to_datetime(frame.pop("ts"), utc=True).astype("datetime64[ns, UTC]")
    frame.index = pd.DatetimeIndex(times)
    if frame.index.isna().any() or (frame.index != frame.index.floor("min")).any():
        raise ValueError(f"{symbol}: invalid Binance 1m open clock")
    frame = frame.loc[(frame.index >= start) & (frame.index < end)].sort_index()
    dup = int(frame.index.duplicated().sum())
    if dup:
        for ts, group in frame.loc[frame.index.duplicated(keep=False)].groupby(level=0):
            if len(group[[*CORE, *[c for c in EXTRA if c in group]]].drop_duplicates()) != 1:
                raise ValueError(f"{symbol}: conflicting duplicate bar at {ts}")
        frame = frame.loc[~frame.index.duplicated()]
    for name in EXTRA:
        if name not in frame:
            frame[name] = np.nan
    bad = (~np.isfinite(frame[CORE])).any(axis=1) | (frame[CORE[:4]] <= 0).any(axis=1)
    bad |= (frame.high < frame[["open", "close", "low"]].max(axis=1))
    bad |= (frame.low > frame[["open", "close", "high"]].min(axis=1)) | (frame.volume < 0)
    source_names = "|".join(sorted(frame["source"].dropna().unique())) if "source" in frame else ""
    notrade = frame["is_trade_bar"].eq(False) if "is_trade_bar" in frame else pd.Series(False,index=frame.index)
    notrade = notrade | frame.trade_count.eq(0)
    good = frame.loc[~(bad | notrade), [*CORE, *EXTRA]].astype(float)
    for col in EXTRA:
        good[col] = good[col].where(np.isfinite(good[col]) & good[col].ge(0))
    good["taker_buy_volume"] = good.taker_buy_volume.where(good.taker_buy_volume <= good.volume * 1.000001)
    good["taker_buy_quote_volume"] = good.taker_buy_quote_volume.where(
        good.taker_buy_quote_volume <= good.quote_volume * 1.000001)
    expected = pd.date_range(start, end, freq="min", inclusive="left")
    first = good.index.min()
    last = good.index.max()
    dense = good.reindex(expected)
    formal = dense.loc[formal_start:]
    coverage = {"symbol": symbol, "bar_first_open": first, "bar_last_open": last,
                "earliest_catalog_partition": min(Path(f.path).parent.name[5:] for f in all_fragments),
                "archive_is_listing_evidence": False, "partitions_read": len(sources),
                "supplemental_partitions_read": sum(source!=root for source,_ in sources),
                "expected_formal_bars": len(formal), "valid_formal_bars": int(formal.close.count()),
                "missing_formal_bars": int(formal.close.isna().sum()), "duplicates_removed": dup,
                "bad_rows": int(bad.sum()), "no_trade_rows": int(notrade.sum()),
                "warmup_valid_bars": int(dense.loc[dense.index < formal_start, "close"].count()),
                "source": source_names, "quantity_unit": "1000PEPE contract units" if symbol == "1000PEPEUSDT" else "base asset units",
                **{f"{col}_valid": int(formal[col].count()) for col in EXTRA}}
    coverage["bars_sha256"] = sha256(pd.util.hash_pandas_object(good, index=True).to_numpy().tobytes()).hexdigest()
    dense.index = bar_available_index(dense.index)
    return dense, coverage, manifest


def marks_archive(paths):
    result = {}
    for path in paths:
        if not Path(path).is_file():
            continue
        for row in json.loads(Path(path).read_text(encoding="utf-8-sig")):
            if row.get("markPrice"):
                key = (row["symbol"], (int(row["fundingTime"]) + 30000) // 60000)
                result.setdefault(key, []).append(row)
    return result


def read_funding(root, symbol, start, end, mark_rows):
    funding_root = symbol_dir(root, symbol) / "data_type=funding_rate"
    rates = []
    for path in sorted(funding_root.glob("freq=*")):
        if not any(start.date().isoformat() <= p.name[5:] <= end.date().isoformat() for p in path.glob("date=*")):
            continue
        filters = {**FILTERS, "symbol": symbol, "data_type": "funding_rate", "freq": path.name[5:]}
        rates.extend(ParquetFundingSource(str(path), symbol, filters, start=start.date(), end=end.date()).stream())
    if not rates:
        return [], 0
    unique = {}
    for event in rates:
        minute = (event.event_time_ns + 30_000_000_000) // 60_000_000_000
        key = minute, event.funding_rate
        rows = mark_rows.get((symbol, minute), [])
        matching = [row for row in rows if np.isclose(float(row["fundingRate"]), event.funding_rate, rtol=0, atol=1e-12)]
        if len({(r["fundingTime"], r["markPrice"]) for r in matching}) == 1:
            row = matching[0]
            event = replace(event, event_time_ns=int(row["fundingTime"])*1_000_000,
                            mark_price=float(row["markPrice"]), source="binance_history_verified_mark")
        existing = unique.get(key)
        if existing is None or event.mark_price is not None:
            unique[key] = event
    kept = sorted((e for e in unique.values() if start.value <= e.event_time_ns <= end.value),
                  key=lambda e: e.event_time_ns)
    return kept, len(rates) - len(unique)
