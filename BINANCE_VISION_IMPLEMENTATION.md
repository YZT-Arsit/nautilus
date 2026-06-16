# Binance Vision Data Source Implementation

## Summary

Successfully implemented Binance Vision historical market data source adapter for the feature_engine framework. This enables importing OHLCV bars from Binance Vision archive for spot, futures_um (USD-M), and futures_cm (COIN-M) markets.

## Files Created

### Core Implementation
1. **feature_engine/data_sources/__init__.py** (23 lines)
   - Package exports for all public APIs

2. **feature_engine/data_sources/binance_vision.py** (427 lines)
   - Complete data source adapter with full type annotations
   - Functions:
     - `build_binance_vision_kline_url()` - Construct Binance Vision download URLs
     - `read_binance_kline_zip()` - Read CSV from ZIP archives
     - `normalize_binance_kline()` - Normalize to StandardBar schema
     - `_detect_timestamp_unit()` - Auto-detect ms vs microseconds
   - Class:
     - `BinanceVisionImporter` - High-level import API
   - Type aliases:
     - `Market` - "spot" | "futures_um" | "futures_cm"
     - `Frequency` - "monthly" | "daily"

3. **feature_engine/data_sources/README.md** (124 lines)
   - Usage documentation with examples
   - Schema reference
   - Supported configurations

### CLI
4. **scripts/ingest_binance_vision.py** (239 lines)
   - Production-ready command-line interface
   - Arguments:
     - `--market` (required) - Market type
     - `--symbol` (required) - Trading pair
     - `--interval` (required) - Bar interval
     - `--frequency` (required) - Download frequency
     - `--start`, `--end` (required) - Date range
     - `--output` (optional) - Output directory
     - `--timeout` (optional) - Download timeout
     - `--overwrite` (optional) - Overwrite existing data
     - `--dry-run` (optional) - Test without writing

### Tests
5. **feature_engine/tests/test_binance_vision.py** (414 lines)
   - 26 comprehensive test cases
   - 5 test classes:
     - `TestBuildUrl` - URL construction (7 tests)
     - `TestReadBinanceKlineZip` - ZIP/CSV reading (5 tests)
     - `TestNormalizeBinanceKline` - Normalization (7 tests)
     - `TestBinanceVisionImporter` - High-level API (5 tests)
     - `TestSchemaValidation` - Output schema (2 tests)
   - All tests use mock data (no real network access)

## Implementation Details

### Markets Supported
- **spot** - Binance spot market
- **futures_um** - USD-M perpetual futures
- **futures_cm** - COIN-M perpetual futures

### Frequencies Supported
- **monthly** - Download monthly archive files (efficient for long ranges)
- **daily** - Download daily archive files (granular)

### Intervals
No hardcoded restrictions. Supports any format including:
- 1m, 5m, 15m, 30m
- 1h, 2h, 4h, 6h, 8h, 12h
- 1d, 1w, 1M
- Any other custom interval

### Timestamp Handling
- Automatically detects millisecond vs microsecond timestamps
- Heuristic: timestamps > 1e13 are treated as microseconds
- Converts to UTC datetime with microsecond precision
- All timestamps stored as `pl.Datetime("us")`

### Output Schema (StandardBar)

| Column | Type | Description |
|--------|------|-------------|
| ts | Datetime(us) | UTC timestamp of bar open |
| exchange | Utf8 | "BINANCE" |
| venue_type | Utf8 | spot, futures_um, or futures_cm |
| symbol | Utf8 | Trading pair (e.g., BTCUSDT) |
| instrument_id | Utf8 | Same as symbol |
| bar_type | Utf8 | Interval (e.g., 1m, 5m, 1h) |
| open | Float64 | Open price |
| high | Float64 | High price |
| low | Float64 | Low price |
| close | Float64 | Close price |
| volume | Float64 | Base asset volume |
| quote_volume | Float64 | Quote asset volume |
| trade_count | Int64 | Number of trades |
| taker_buy_volume | Float64 | Taker buy base asset volume |
| taker_buy_quote_volume | Float64 | Taker buy quote asset volume |
| source | Utf8 | "binance_vision" |
| ingested_at | Datetime(us) | When data was ingested |

### Output Partitioning (Hive-style)

```
output/
  exchange=BINANCE/
    venue_type=spot/
      symbol=BTCUSDT/
        bar_type=1m/
          date=2024-01-01/
            part-000.parquet
          date=2024-01-02/
            part-000.parquet
```

### Validation & Error Handling

**Data Validation:**
- OHLC constraint: high >= low
- Monotonic increasing timestamps
- No null values in OHLC fields

**Error Handling:**
- HTTP errors show URL and status code
- Clear date format validation messages
- Network timeout configurable
- Network failures recoverable with partial data
- Invalid ZIP/CSV format detection

**Code Quality:**
- Full type annotations (Python 3.10+ compatible)
- Lazy Polars/PyArrow imports (only when needed)
- Clear error messages for debugging
- Comprehensive docstrings
- No external dependencies beyond standard Polars/PyArrow

## Usage Examples

### Python API
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

### Command Line
```bash
# Single day
python scripts/ingest_binance_vision.py \
    --market spot \
    --symbol BTCUSDT \
    --interval 1m \
    --frequency daily \
    --start 2024-01-01 \
    --end 2024-01-31 \
    --output historical_data/market_data

# Monthly data (larger files, faster)
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

# Overwrite existing
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

## Testing

All 26 tests pass and cover:
- ✓ URL construction for all markets and frequencies
- ✓ ZIP file reading and CSV parsing
- ✓ Millisecond and microsecond timestamp conversion
- ✓ StandardBar schema validation
- ✓ Data constraints (OHLC, monotonic timestamps)
- ✓ Date range generation (daily/monthly/year boundaries)
- ✓ Error handling (invalid inputs, network errors)

Mock ZIP files are used in tests - no real network access required.

## Integration with Feature Engine

The implementation follows feature_engine conventions:
- Uses Polars for data handling (lazy evaluation where possible)
- Hive partitioning matches existing market_data layout
- Adheres to StandardBar schema
- No modifications to feature operator layer
- Can be used standalone or integrated into pipelines

## Notes

- **Rust compilation not required** - This is pure Python (with standard Polars/PyArrow)
- **Polars 0.20+** - Required for type annotations and schema operations
- **PyArrow 10+** - Required for Hive Parquet writing
- **Network access** - Only needed for actual data download (not for tests)
- **Concurrency** - Single-threaded by design, can be parallelized by caller if needed

## Future Enhancements

Potential additions (not implemented):
- Parallel downloads for multiple dates
- Incremental update support (skip existing dates)
- Data deduplication across daily/monthly boundaries
- Compression settings for output Parquet
- Checksums/integrity verification
- ORC format support (in addition to Parquet)