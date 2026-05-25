"""Aggregate real Nautilus QuoteTick parquet files into OHLCV bars for engineering tests.

The generated ``volume`` is the count of quote ticks in each interval. It is
synthetic quote activity, not traded volume, and must not be used for formal
strategy performance evaluation.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd


DEFAULT_CATALOG_PATH = r"D:\QuanHub\DataHome\DataTrans\nautilus_catalog"


def _decode_fixed_precision(value, scale: float) -> float:
    if isinstance(value, memoryview):
        value = value.tobytes()
    if isinstance(value, (bytes, bytearray)):
        return int.from_bytes(value, byteorder="little", signed=True) / scale
    return float(pd.to_numeric(value, errors="coerce"))


def _load_quote_ticks(catalog_path: Path, instrument_id: str) -> tuple[pd.DataFrame, list[Path]]:
    paths = sorted(catalog_path.rglob(f"quote_tick/{instrument_id}/*.parquet"))
    if not paths:
        raise ValueError(
            f"No QuoteTick parquet files found for instrument_id={instrument_id!r} "
            f"under catalog_path={catalog_path}."
        )

    frames = [pd.read_parquet(path) for path in paths]
    return pd.concat(frames, ignore_index=True), paths


def aggregate_quote_ticks(
    quote_ticks: pd.DataFrame,
    instrument_id: str,
    bar_interval: str,
    price_scale: float = 1_000_000_000,
) -> pd.DataFrame:
    required = {"bid_price", "ask_price", "ts_event"}
    missing = sorted(required.difference(quote_ticks.columns))
    if missing:
        raise ValueError(f"QuoteTick input is missing required fields: {missing}.")

    df = quote_ticks.copy()
    df["timestamp"] = pd.to_datetime(df["ts_event"], unit="ns", utc=True, errors="coerce")
    df["bid_price"] = df["bid_price"].map(lambda value: _decode_fixed_precision(value, price_scale))
    df["ask_price"] = df["ask_price"].map(lambda value: _decode_fixed_precision(value, price_scale))
    df = df[
        df["timestamp"].notna()
        & (df["bid_price"] > 0)
        & (df["ask_price"] > 0)
        & (df["ask_price"] >= df["bid_price"])
    ].copy()
    if df.empty:
        raise ValueError("No valid QuoteTick rows after timestamp and bid/ask parsing.")

    df["mid_price"] = (df["bid_price"] + df["ask_price"]) / 2.0
    grouped = df.set_index("timestamp").sort_index().resample(bar_interval)["mid_price"]
    bars = grouped.agg(open="first", high="max", low="min", close="last", volume="count")
    bars = bars.dropna(subset=["open", "high", "low", "close"]).reset_index()
    if bars.empty:
        raise ValueError(f"No bars generated for bar_interval={bar_interval!r}.")

    bars["volume"] = bars["volume"].astype(float)
    bars["symbol"] = instrument_id
    bars["volume_source"] = "synthetic_tick_count"
    bars["bar_interval"] = bar_interval
    return bars[
        [
            "timestamp",
            "open",
            "high",
            "low",
            "close",
            "volume",
            "symbol",
            "volume_source",
            "bar_interval",
        ]
    ]


def _write_output(bars: pd.DataFrame, output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if output_path.suffix.lower() == ".csv":
        bars.to_csv(output_path, index=False)
    elif output_path.suffix.lower() == ".parquet":
        bars.to_parquet(output_path, index=False)
    else:
        raise ValueError("output_path must use a .csv or .parquet suffix.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate OHLCV bars from Nautilus QuoteTick data.")
    parser.add_argument("--catalog-path", default=DEFAULT_CATALOG_PATH)
    parser.add_argument("--instrument-id", default="IH2303.CFFEX")
    parser.add_argument("--bar-interval", default="1min")
    parser.add_argument("--output-path", required=True)
    parser.add_argument("--price-scale", type=float, default=1_000_000_000)
    args = parser.parse_args()

    quote_ticks, source_files = _load_quote_ticks(Path(args.catalog_path), args.instrument_id)
    bars = aggregate_quote_ticks(
        quote_ticks=quote_ticks,
        instrument_id=args.instrument_id,
        bar_interval=args.bar_interval,
        price_scale=args.price_scale,
    )
    output_path = Path(args.output_path)
    _write_output(bars, output_path)

    print(f"source_files: {len(source_files)}")
    print(f"input_rows: {len(quote_ticks)}")
    print(f"output_bars: {len(bars)}")
    print(f"time_range: {bars['timestamp'].iloc[0]} -> {bars['timestamp'].iloc[-1]}")
    print(f"output_path: {output_path}")
    print("volume_source: synthetic_tick_count")
    print("WARNING: volume is synthetic quote tick_count, not traded volume.")
    print("WARNING: generated bars are for engineering validation only, not performance evaluation.")


if __name__ == "__main__":
    main()
