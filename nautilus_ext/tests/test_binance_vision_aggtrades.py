"""Tests for the Binance Vision aggTrades adapter (self-owned data ingestion).

Covers URL construction, mock-ZIP CSV parsing (incl. header skip and the
futures 7-field variant), and StandardTrade normalization (side derivation,
quote_quantity, monotonic ts). normalize_* needs polars (server-only) and is
importorskip-gated. No nautilus_trader involvement in data ingestion.
"""
from __future__ import annotations

import io
import zipfile

import pytest

from feature_engine.data_sources.binance_vision import (
    build_binance_vision_aggtrades_url,
    normalize_binance_aggtrades,
    read_binance_aggtrades_zip,
)


def _zip_bytes(csv_text: str, name: str = "BTCUSDT-aggTrades-2024-06-01.csv") -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(name, csv_text)
    return buf.getvalue()


def test_build_aggtrades_url_spot_and_futures():
    assert build_binance_vision_aggtrades_url("spot", "BTCUSDT", "daily", "2024-06-01") == (
        "https://data.binance.vision/data/spot/daily/aggTrades/BTCUSDT/"
        "BTCUSDT-aggTrades-2024-06-01.zip"
    )
    assert "futures/um" in build_binance_vision_aggtrades_url(
        "futures_um", "BTCUSDT", "monthly", "2024-06"
    )
    with pytest.raises(ValueError):
        build_binance_vision_aggtrades_url("spot", "BTCUSDT", "daily", "2024-6-1")


def test_read_aggtrades_zip_spot_format():
    # spot: aggId, price, qty, firstId, lastId, ts(ms), isBuyerMaker, isBestMatch
    csv = (
        "0,100.0,2.0,0,0,1717200000000,true,true\n"
        "1,101.0,3.0,1,1,1717200001000,false,true\n"
    )
    rows = read_binance_aggtrades_zip(_zip_bytes(csv))
    assert len(rows) == 2
    assert rows[0]["agg_trade_id"] == 0
    assert rows[0]["price"] == 100.0
    assert rows[0]["quantity"] == 2.0
    assert rows[0]["is_buyer_maker"] is True
    assert rows[1]["is_buyer_maker"] is False


def test_read_aggtrades_zip_skips_header_and_handles_7_field():
    csv = (
        "agg_trade_id,price,quantity,first,last,ts,is_buyer_maker\n"  # header -> skipped
        "5,200.0,1.5,5,5,1717200002000,true\n"                        # futures: 7 fields
    )
    rows = read_binance_aggtrades_zip(_zip_bytes(csv))
    assert len(rows) == 1
    assert rows[0]["agg_trade_id"] == 5
    assert rows[0]["is_best_match"] is None


def test_normalize_aggtrades_to_standard_trade():
    pytest.importorskip("polars")
    csv = (
        "0,100.0,2.0,0,0,1717200000000,true,true\n"
        "1,110.0,4.0,1,1,1717200001000,false,true\n"
    )
    rows = read_binance_aggtrades_zip(_zip_bytes(csv))
    df = normalize_binance_aggtrades(rows, market="spot", symbol="BTCUSDT")

    expected_cols = {
        "ts", "exchange", "venue_type", "symbol", "instrument_id", "agg_trade_id",
        "price", "quantity", "quote_quantity", "first_trade_id", "last_trade_id",
        "is_buyer_maker", "side", "source", "ingested_at",
    }
    assert expected_cols.issubset(set(df.columns))
    assert df.height == 2
    assert df["source"][0] == "binance_vision_aggTrades"
    # side derivation
    assert df["side"][0] == "SELL"   # is_buyer_maker True
    assert df["side"][1] == "BUY"    # is_buyer_maker False
    # quote_quantity = price * quantity
    assert df["quote_quantity"][0] == pytest.approx(200.0)
    assert df["quote_quantity"][1] == pytest.approx(440.0)
    # ts is monotonic and the first ts is 2024-06-01T00:00:00Z
    assert df["ts"].is_sorted()
    assert str(df["ts"][0]) == "2024-06-01 00:00:00"
