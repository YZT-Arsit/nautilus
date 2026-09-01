"""Binance Vision USD-M raw-trade archive adapter.

Downloads one official daily ZIP, verifies its published SHA-256 checksum, and
normalizes CSV rows into the existing :class:`TradeEvent` contract.  Storage and
bar construction remain outside this adapter.
"""

from __future__ import annotations

import csv
import hashlib
import io
import math
import os
import time
import zipfile
from collections.abc import Iterator
from datetime import date
from itertools import chain
from pathlib import Path
from urllib.request import urlopen

from data_engine.adapters.trade_adapter import make_trade_event
from data_engine.events import TradeEvent


BASE_URL = "https://data.binance.vision/data/futures/um/daily/trades"


def raw_trade_archive_url(symbol: str, day: date) -> str:
    name = f"{symbol}-trades-{day.isoformat()}.zip"
    return f"{BASE_URL}/{symbol}/{name}"


def _published_checksum(url: str, *, timeout: int) -> str:
    with urlopen(url + ".CHECKSUM", timeout=timeout) as response:
        text = response.read().decode("utf-8").strip()
    value = text.split()[0].lower()
    if len(value) != 64 or any(ch not in "0123456789abcdef" for ch in value):
        raise ValueError(f"invalid Binance checksum payload for {url}: {text!r}")
    return value


def _download_verified_archive_once(
    *,
    symbol: str,
    day: date,
    cache_root: Path,
    timeout: int = 120,
) -> tuple[Path, str]:
    """Download one daily archive atomically and verify Binance's checksum."""
    url = raw_trade_archive_url(symbol, day)
    expected = _published_checksum(url, timeout=timeout)
    cache_root.mkdir(parents=True, exist_ok=True)
    destination = cache_root / url.rsplit("/", 1)[-1]
    if destination.is_file():
        digest = hashlib.sha256(destination.read_bytes()).hexdigest()
        if digest == expected:
            return destination, expected
        destination.unlink()
    temporary = destination.with_suffix(destination.suffix + ".part")
    digest = hashlib.sha256()
    try:
        with urlopen(url, timeout=timeout) as response, temporary.open("wb") as output:
            while chunk := response.read(8 * 1024 * 1024):
                output.write(chunk)
                digest.update(chunk)
        actual = digest.hexdigest()
        if actual != expected:
            raise ValueError(f"checksum mismatch for {url}: expected {expected}, got {actual}")
        os.replace(temporary, destination)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    return destination, expected


def download_verified_archive(
    *,
    symbol: str,
    day: date,
    cache_root: Path,
    timeout: int = 120,
    retries: int = 3,
) -> tuple[Path, str]:
    """Download with bounded retries for transient official-archive failures."""
    if retries < 1:
        raise ValueError("retries must be positive")
    last_error: Exception | None = None
    for attempt in range(retries):
        try:
            return _download_verified_archive_once(
                symbol=symbol,
                day=day,
                cache_root=cache_root,
                timeout=timeout,
            )
        except Exception as exc:
            last_error = exc
            if attempt + 1 < retries:
                time.sleep(2**attempt)
    assert last_error is not None
    raise last_error


def iter_raw_trade_archive(
    archive: Path,
    *,
    symbol: str,
    validate_order: bool = True,
) -> Iterator[TradeEvent]:
    """Stream one verified archive in source-row order as TradeEvents.

    ``validate_order`` is an audit option, not a source guarantee.  At least one
    official daily futures ``trades`` archive contains neighbouring trade IDs
    whose timestamps are not chronological.  Callers that require deterministic
    chronological order must either use :func:`read_raw_trade_archive` or perform
    a bounded exact aggregation that explicitly orders by timestamp/trade ID.
    """
    prior_key: tuple[int, int] | None = None
    with zipfile.ZipFile(archive) as bundle:
        members = [name for name in bundle.namelist() if name.lower().endswith(".csv")]
        if len(members) != 1:
            raise ValueError(f"expected one CSV in {archive}, found {members}")
        with (
            bundle.open(members[0]) as raw,
            io.TextIOWrapper(raw, encoding="utf-8", newline="") as text,
        ):
            reader = csv.reader(text)
            first = next(reader, None)
            if first is None:
                raise ValueError(f"empty raw trade CSV in {archive}")
            expected_header = ["id", "price", "qty", "quote_qty", "time", "is_buyer_maker"]
            rows = (
                reader
                if [value.strip().lower() for value in first] == expected_header
                else chain([first], reader)
            )
            for row in rows:
                if len(row) != 6:
                    raise ValueError(f"unexpected raw trade row in {archive}: {row[:8]}")
                trade_id, price, quantity, quote_quantity, timestamp, maker = row
                maker_text = maker.strip().lower()
                if maker_text not in {"true", "false"}:
                    raise ValueError(f"invalid is_buyer_maker value: {maker_text!r}")
                price_value = float(price)
                quantity_value = float(quantity)
                quote_quantity_value = float(quote_quantity)
                if not all(
                    math.isfinite(value) and value >= 0
                    for value in (price_value, quantity_value, quote_quantity_value)
                ):
                    raise ValueError(f"invalid numeric raw trade row in {archive}: {row[:6]}")
                event_time_ns = int(timestamp) * 1_000_000
                trade_id_value = int(trade_id)
                key = (event_time_ns, trade_id_value)
                if validate_order and prior_key is not None and key <= prior_key:
                    raise ValueError(
                        f"raw trade source order violation in {archive}: {key} <= {prior_key}"
                    )
                prior_key = key
                yield make_trade_event(
                    price=price_value,
                    quantity=quantity_value,
                    quote_quantity=quote_quantity_value,
                    quote_quantity_source="source_quote_qty",
                    instrument_id=symbol,
                    event_time_ns=event_time_ns,
                    is_buyer_maker=maker_text == "true",
                    trade_id=trade_id_value,
                    source="binance_vision_raw_trades",
                )


def read_raw_trade_archive(
    archive: Path,
    *,
    symbol: str,
) -> list[TradeEvent]:
    """Normalize one verified Binance raw-trade ZIP to sorted TradeEvents."""
    trades = list(iter_raw_trade_archive(archive, symbol=symbol, validate_order=False))
    trades.sort(key=lambda trade: (trade.event_time_ns, int(trade.trade_id)))
    return trades


def download_and_read_raw_trades(
    *,
    symbol: str,
    day: date,
    cache_root: Path,
    timeout: int = 120,
) -> tuple[list[TradeEvent], Path, str]:
    archive, checksum = download_verified_archive(
        symbol=symbol,
        day=day,
        cache_root=cache_root,
        timeout=timeout,
    )
    return read_raw_trade_archive(archive, symbol=symbol), archive, checksum


__all__ = [
    "download_and_read_raw_trades",
    "download_verified_archive",
    "iter_raw_trade_archive",
    "raw_trade_archive_url",
    "read_raw_trade_archive",
]
