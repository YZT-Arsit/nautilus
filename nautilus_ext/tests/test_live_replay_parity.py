"""Replay-parity tests: historical TradeEvent vs live-normalized TradeEvent.

Mock StandardTrade rows -> synthetic Binance aggTrade message -> LiveNormalizer
-> TradeEvent, compared field-by-field against the TradeEvent the historical
loader would produce (built with the same ``make_trade_event`` adapter).

Pure stdlib: no network, no pyarrow, no Nautilus.
"""
from __future__ import annotations

import pytest

from data_engine.adapters.trade_adapter import make_trade_event
from data_engine.events import TradeEvent
from data_engine.live import (
    LiveNormalizer,
    compare_trade_events,
    normalize_message,
    standard_trade_to_agg_message,
)

# Mock StandardTrade rows (one BUY, one SELL); event_time has sub-ms detail.
_ROWS = [
    dict(event_time_ns=1_780_000_000_651_471_000, price=65000.10, quantity=0.5,
         agg_trade_id=111, is_buyer_maker=True, first_trade_id=1, last_trade_id=3),
    dict(event_time_ns=1_780_000_001_123_000_000, price=65010.25, quantity=1.25,
         agg_trade_id=112, is_buyer_maker=False, first_trade_id=4, last_trade_id=4),
]
_SYMBOL = "BTCUSDT"


def _historical_event(row) -> TradeEvent:
    """What the historical loader (parquet_trades) produces for this row."""
    return make_trade_event(
        price=row["price"], quantity=row["quantity"],
        instrument_id=f"{_SYMBOL}.BINANCE", event_time_ns=row["event_time_ns"],
        is_buyer_maker=row["is_buyer_maker"], trade_id=row["agg_trade_id"],
        source="parquet_trades",
    )


def _live_event(row, *, wrap=False) -> TradeEvent:
    msg = standard_trade_to_agg_message(symbol=_SYMBOL, wrap=wrap, **row)
    return LiveNormalizer().normalize(msg)


def test_parity_per_row_matches():
    for row in _ROWS:
        a = _historical_event(row)
        b = _live_event(row)
        ok, diffs = compare_trade_events(a, b)
        assert ok, f"mismatch: {diffs}"


def test_parity_covers_side_and_quote_quantity():
    buy = _live_event(_ROWS[1])
    sell = _live_event(_ROWS[0])
    assert buy.side == "BUY" and sell.side == "SELL"          # derived from m
    assert buy.is_buyer_maker is False and sell.is_buyer_maker is True
    # quote_quantity = price * quantity in both paths
    assert sell.quote_quantity == pytest.approx(65000.10 * 0.5)
    assert _historical_event(_ROWS[0]).quote_quantity == pytest.approx(sell.quote_quantity)


def test_parity_event_time_at_ms_resolution():
    row = _ROWS[0]                       # has sub-ms detail (.651471)
    a, b = _historical_event(row), _live_event(row)
    # exact ns differs (archive us vs live ms) ...
    assert a.event_time_ns != b.event_time_ns
    assert a.event_time_ns % 1_000_000 != 0          # archive carried sub-ms
    assert b.event_time_ns % 1_000_000 == 0          # live WS is ms-aligned
    # ... but parity holds at millisecond resolution
    assert a.event_time_ns // 1_000_000 == b.event_time_ns // 1_000_000
    assert compare_trade_events(a, b)[0]


def test_parity_through_combined_stream_wrapper():
    row = _ROWS[0]
    a = _historical_event(row)
    b = _live_event(row, wrap=True)      # envelope unwrapped by normalize_message
    assert isinstance(b, TradeEvent)
    assert compare_trade_events(a, b)[0]


def test_source_difference_is_expected_and_excluded():
    a, b = _historical_event(_ROWS[0]), _live_event(_ROWS[0])
    assert a.source == "parquet_trades"
    assert b.source == "binance_ws_aggTrade"
    # ignored by default ...
    assert compare_trade_events(a, b, ignore_source=True)[0]
    # ... but surfaced when explicitly compared
    ok, diffs = compare_trade_events(a, b, ignore_source=False)
    assert not ok and any(d[0] == "source" for d in diffs)


def test_mismatch_is_reported():
    a = _historical_event(_ROWS[0])
    bad = _live_event({**_ROWS[0], "price": 1.0})   # corrupt price
    ok, diffs = compare_trade_events(a, bad)
    assert not ok and any(d[0] == "price" for d in diffs)


def test_normalize_message_path_equivalent_to_live_normalizer():
    row = _ROWS[0]
    msg = standard_trade_to_agg_message(symbol=_SYMBOL, **row)
    assert compare_trade_events(_historical_event(row), normalize_message(msg))[0]


def test_no_nautilus_or_network_import_in_replay():
    import inspect

    from data_engine.live import replay

    src = inspect.getsource(replay)
    assert "import nautilus_trader" not in src
    assert "from nautilus_trader" not in src
    for forbidden in ("import websocket", "import websockets", "import asyncio",
                      "from urllib", "import urllib", "import aiohttp"):
        assert forbidden not in src
