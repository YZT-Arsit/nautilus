# Data Sources

This module contains adapters for importing historical market data from various sources
and normalizing them to a standard schema for use with the feature engine.

## Binance Vision

Import OHLCV bars from Binance Vision archive for spot, USD-M futures, or COIN-M futures markets.

### Quick Start

```python
from feature_engine.data_sources import BinanceVisionImporter

importer = BinanceVisionImporter()
df = importer.import_period(
    market="spot",
    symbol="BTCUSDT",
    interval="1m",
    frequency="daily",
    start_date="2024-01-01",
    end_date="2024-01-31",
)
# df is a Polars DataFrame with StandardBar schema
```

### CLI Usage

```bash
# Import daily bars for one month
python scripts/ingest_binance_vision.py \
    --market spot \
    --symbol BTCUSDT \
    --interval 1m \
    --frequency daily \
    --start 2024-01-01 \
    --end 2024-01-31 \
    --output historical_data/market_data

# Import monthly data (larger files, faster for long ranges)
python scripts/ingest_binance_vision.py \
    --market futures_um \
    --symbol ETHUSDT \
    --interval 1h \
    --frequency monthly \
    --start 2024-01 \
    --end 2024-06 \
    --output historical_data/market_data

# Dry run (test without writing)
python scripts/ingest_binance_vision.py \
    --market spot \
    --symbol BTCUSDT \
    --interval 5m \
    --frequency daily \
    --start 2024-06-01 \
    --end 2024-06-05 \
    --dry-run

# Overwrite existing data
python scripts/ingest_binance_vision.py \
    --market spot \
    --symbol BTCUSDT \
    --interval 1m \
    --frequency monthly \
    --start 2024-01 \
    --end 2024-12 \
    --output historical_data/market_data \
    --overwrite
```

### Output Schema

All data is normalized to StandardBar schema:

| Column | Type | Description |
|--------|------|-------------|
| ts | datetime | UTC timestamp of bar open |
| exchange | string | "BINANCE" |
| venue_type | string | spot, futures_um, futures_cm |
| symbol | string | Trading pair (e.g., BTCUSDT) |
| instrument_id | string | Same as symbol |
| bar_type | string | Interval (e.g., 1m, 5m, 1h) |
| open | float64 | Open price |
| high | float64 | High price |
| low | float64 | Low price |
| close | float64 | Close price |
| volume | float64 | Base asset volume |
| quote_volume | float64 | Quote asset volume |
| trade_count | int64 | Number of trades |
| taker_buy_volume | float64 | Taker buy base asset volume |
| taker_buy_quote_volume | float64 | Taker buy quote asset volume |
| source | string | "binance_vision" |
| ingested_at | datetime | Timestamp when data was ingested |

### Output Partitioning

Data is written in Hive-style partitions:

```
output/
  exchange=BINANCE/
    venue_type=spot/
      symbol=BTCUSDT/
        bar_type=1m/
          date=2024-01-01/
            part-000.parquet
```

### Supported Markets

- **spot**: Spot market data
- **futures_um**: USD-M perpetual futures
- **futures_cm**: COIN-M perpetual futures

### Download Frequency

- **monthly**: Download monthly archive files (more efficient for long date ranges)
- **daily**: Download daily archive files (more granular, larger number of files)

### Timestamp Handling

The importer automatically detects whether timestamps are in milliseconds or microseconds
(based on magnitude) and converts them to UTC datetime with microsecond precision.