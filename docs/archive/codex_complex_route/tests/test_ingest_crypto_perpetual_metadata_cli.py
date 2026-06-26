from __future__ import annotations

from pathlib import Path

import pytest

from scripts import ingest_crypto_perpetual_metadata as cli


def _exchange_info_payload(symbol: str) -> dict[str, object]:
    return {
        "symbols": [
            {
                "symbol": symbol,
                "contractType": "PERPETUAL",
                "baseAsset": symbol.replace("USDT", ""),
                "quoteAsset": "USDT",
                "marginAsset": "USDT",
                "pricePrecision": 2,
                "quantityPrecision": 3,
                "status": "TRADING",
                "filters": [
                    {"filterType": "PRICE_FILTER", "tickSize": "0.10"},
                    {"filterType": "LOT_SIZE", "stepSize": "0.001", "minQty": "0.001"},
                    {"filterType": "MIN_NOTIONAL", "notional": "100"},
                ],
            }
        ]
    }


def _fake_fetch(url: str, *, timeout: int):
    if "exchangeInfo" in url:
        return {"symbols": _exchange_info_payload("BTCUSDT")["symbols"] + _exchange_info_payload("ETHUSDT")["symbols"]}
    if "fundingRate" in url:
        return [{"symbol": "BTCUSDT", "fundingRate": "0.0001", "fundingTime": 1717200000000}]
    if "markPriceKlines" in url:
        return [[1717200000000, "67000", "67100", "66900", "67050", "0", 1717200299999, "0", 0, "0", "0", "0"]]
    if "indexPriceKlines" in url:
        return [[1717200000000, "66990", "67090", "66890", "67040", "0", 1717200299999, "0", 0, "0", "0", "0"]]
    raise AssertionError(url)


def _fake_funding_archive(url: str, *, symbol: str, day: str, timeout: int):
    assert "data.binance.vision" in url
    return [{"symbol": symbol, "fundingRate": "0.0001", "fundingTime": 1717200000000}]


def _fake_kline_archive(url: str, *, timeout: int):
    assert "data.binance.vision" in url
    if "markPriceKlines" in url:
        close = "67050"
    else:
        close = "67040"
    return [[1717200000000, "67000", "67100", "66900", close, "0", 1717200299999, "0", 0, "0", "0", "0"]]


def test_plan_only_writes_nothing(tmp_path):
    rc = cli.main(
        [
            "--symbols",
            "BTCUSDT,ETHUSDT",
            "--date",
            "2024-06-01",
            "--metadata-types",
            "exchange_info,funding_rate,mark_price",
            "--out-root",
            str(tmp_path),
            "--plan-only",
        ]
    )
    assert rc == 0
    assert not any(tmp_path.rglob("*"))


def test_dry_run_writes_nothing(tmp_path):
    rc = cli.main(
        [
            "--symbols",
            "BTCUSDT",
            "--date",
            "2024-06-01",
            "--metadata-types",
            "exchange_info",
            "--out-root",
            str(tmp_path),
            "--dry-run",
        ]
    )
    assert rc == 0
    assert not any(tmp_path.rglob("*"))


def test_max_symbols_guard(tmp_path):
    with pytest.raises(ValueError, match="max-symbols"):
        cli.main(
            [
                "--symbols",
                "BTCUSDT,ETHUSDT,SOLUSDT,BNBUSDT,XRPUSDT",
                "--date",
                "2024-06-01",
                "--out-root",
                str(tmp_path),
            ]
        )


def test_existing_root_uses_suffix(tmp_path, monkeypatch):
    pyarrow = pytest.importorskip("pyarrow")
    assert pyarrow is not None
    (tmp_path / "keep.txt").write_text("existing", encoding="utf-8")
    monkeypatch.setattr(cli, "_fetch_json", _fake_fetch)
    monkeypatch.setattr(cli, "_fetch_funding_archive", _fake_funding_archive)
    monkeypatch.setattr(cli, "_fetch_kline_archive", _fake_kline_archive)
    rc = cli.main(
        [
            "--symbols",
            "BTCUSDT",
            "--date",
            "2024-06-01",
            "--metadata-types",
            "exchange_info",
            "--out-root",
            str(tmp_path),
            "--max-symbols",
            "1",
        ]
    )
    assert rc == 0
    assert (tmp_path / "keep.txt").read_text(encoding="utf-8") == "existing"
    suffix_root = tmp_path.with_name(f"{tmp_path.name}_2")
    assert (suffix_root / "exchange=BINANCE" / "venue_type=futures_um" / "symbol=BTCUSDT").exists()


def test_build_plan_public_endpoint_and_output_paths(tmp_path):
    plan = cli.build_plan(
        symbols=["BTCUSDT"],
        day="2024-06-01",
        metadata_types=("exchange_info", "funding_rate", "mark_price"),
        out_root=tmp_path,
    )
    by_type = {item.metadata_type: item for item in plan}
    assert by_type["exchange_info"].endpoints == ("https://fapi.binance.com/fapi/v1/exchangeInfo",)
    assert "data.binance.vision" in by_type["funding_rate"].endpoints[0]
    assert "fundingRate" in by_type["funding_rate"].endpoints[0]
    assert "fapi.binance.com" in by_type["funding_rate"].endpoints[1]
    assert len(by_type["mark_price"].endpoints) == 4
    assert "data.binance.vision" in by_type["mark_price"].endpoints[0]
    assert "markPriceKlines" in by_type["mark_price"].endpoints[0]
    assert "indexPriceKlines" in by_type["mark_price"].endpoints[1]
    exchange_path = by_type["exchange_info"].output_path.replace("\\", "/")
    funding_path = by_type["funding_rate"].output_path.replace("\\", "/")
    assert exchange_path.endswith("metadata_type=exchange_info/snapshot.json")
    assert funding_path.endswith("metadata_type=funding_rate/date=2024-06-01/part-0.parquet")


def test_metadata_type_validation(tmp_path):
    with pytest.raises(ValueError, match="unsupported metadata"):
        cli.main(
            [
                "--symbols",
                "BTCUSDT",
                "--date",
                "2024-06-01",
                "--metadata-types",
                "exchange_info,liquidation",
                "--out-root",
                str(tmp_path),
                "--plan-only",
            ]
        )


def test_execute_plan_schema_validation(tmp_path, monkeypatch):
    pyarrow = pytest.importorskip("pyarrow.parquet")
    monkeypatch.setattr(cli, "_fetch_json", _fake_fetch)
    monkeypatch.setattr(cli, "_fetch_funding_archive", _fake_funding_archive)
    monkeypatch.setattr(cli, "_fetch_kline_archive", _fake_kline_archive)
    plan = cli.build_plan(
        symbols=["BTCUSDT"],
        day="2024-06-01",
        metadata_types=("exchange_info", "funding_rate", "mark_price"),
        out_root=tmp_path,
    )
    results = cli.execute_plan(plan, timeout=1, no_overwrite=True)
    assert [row.status for row in results] == ["downloaded", "downloaded", "downloaded"]
    assert [row.rows for row in results] == [1, 1, 1]
    funding_path = Path([row.output_path for row in results if row.metadata_type == "funding_rate"][0])
    mark_path = Path([row.output_path for row in results if row.metadata_type == "mark_price"][0])
    assert pyarrow.ParquetFile(funding_path).read().num_rows == 1
    assert pyarrow.ParquetFile(mark_path).read().column_names == [
        "ts",
        "exchange",
        "venue_type",
        "symbol",
        "instrument_id",
        "mark_price",
        "index_price",
        "estimated_settle_price",
        "last_funding_rate",
        "next_funding_time",
        "source",
        "ingested_at",
    ]


def test_existing_output_skip_no_overwrite(tmp_path):
    plan = cli.build_plan(
        symbols=["BTCUSDT"],
        day="2024-06-01",
        metadata_types=("exchange_info",),
        out_root=tmp_path,
    )
    out = Path(plan[0].output_path)
    out.parent.mkdir(parents=True)
    out.write_text("existing", encoding="utf-8")
    results = cli.execute_plan(plan, timeout=1, no_overwrite=True)
    assert results[0].status == "skipped_existing"
    assert out.read_text(encoding="utf-8") == "existing"


def test_cli_has_no_disallowed_endpoint_terms():
    text = Path("scripts/ingest_crypto_perpetual_metadata.py").read_text(encoding="utf-8")
    forbidden = (
        "api" + "Key",
        "sign" + "ature",
        "listen" + "Key",
        "priv" + "ate",
        "acc" + "ount",
        "bal" + "ance",
        "pos" + "ition",
        "lev" + "erage",
        "margin" + "Type",
        "user" + "DataStream",
        "create_" + "order",
        "shell" + "=True",
    )
    for token in forbidden:
        assert token not in text
