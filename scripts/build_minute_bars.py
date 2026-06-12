#!/usr/bin/env python3
"""CLI：构建分钟线并写入 market_data（Hive Parquet）。

只负责 argparse + 读取输入 + 调用 :class:`feature_engine.services.MinuteBarBuilder`，
业务逻辑都在 service 里。

输入支持：
* ``--input x.csv``      —— 标准库 csv，逐行当作 tick/bar（需含价格与时间戳列）。
* ``--input x.parquet``  —— pyarrow 懒加载读取。
* Nautilus catalog       —— 见 ``data_engine/adapters/nautilus_catalog.py``
  （可选，不在本 CLI 默认路径）。

示例::

    python scripts/build_minute_bars.py \
        --input ticks.csv \
        --output-root historical_data/market_data \
        --instrument-id IH2303.CFFEX \
        --frequency 1m \
        --trading-date 2026-05-26 \
        --asset-class future --exchange CFFEX
"""
from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from feature_engine.services import MinuteBarBuilder  # noqa: E402


def _read_csv_ticks(path: Path) -> list[dict]:
    with open(path, newline="") as fh:
        return [dict(row) for row in csv.DictReader(fh)]


def _read_parquet_ticks(path: Path) -> list[dict]:
    try:
        import pyarrow.parquet as pq  # noqa: PLC0415
    except ImportError as exc:  # pragma: no cover
        raise ImportError("读取 parquet 输入需要 pyarrow。") from exc
    return pq.read_table(str(path)).to_pylist()


def _load_ticks(input_path: str) -> list[dict]:
    p = Path(input_path)
    if not p.exists():
        raise FileNotFoundError(f"输入不存在：{p}")
    if p.suffix.lower() == ".csv":
        return _read_csv_ticks(p)
    if p.suffix.lower() in (".parquet", ".pq"):
        return _read_parquet_ticks(p)
    raise ValueError(f"暂不支持的输入类型 {p.suffix!r}（支持 .csv / .parquet）")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="构建分钟线并写入 market_data")
    ap.add_argument("--input", required=True, help="csv / parquet 输入路径")
    ap.add_argument("--output-root", required=True, help="market_data 根目录")
    ap.add_argument("--instrument-id", required=True)
    ap.add_argument("--frequency", default="1m")
    ap.add_argument("--trading-date", default=None, help="覆盖交易日（缺省按 UTC 日期推导）")
    ap.add_argument("--asset-class", default="future")
    ap.add_argument("--exchange", default="UNKNOWN")
    ap.add_argument("--price-field", default=None, help="价格列名（缺省自动探测）")
    ap.add_argument("--size-field", default=None, help="成交量列名（缺省自动探测）")
    ap.add_argument("--no-strict", action="store_true", help="跳过校验失败即拒写的保护")
    args = ap.parse_args(argv)

    ticks = _load_ticks(args.input)
    builder = MinuteBarBuilder(asset_class=args.asset_class, exchange=args.exchange)
    result, paths = builder.build_and_write(
        ticks,
        instrument_id=args.instrument_id,
        market_root=args.output_root,
        frequency=args.frequency,
        trading_date=args.trading_date,
        price_field=args.price_field,
        size_field=args.size_field,
        strict=not args.no_strict,
    )
    print(f"[build_minute_bars] {len(result.bars)} 根 {args.frequency} bar "
          f"({args.instrument_id})，写出 {len(paths)} 个 parquet 文件")
    print(f"  volume_is_synthetic={result.volume_is_synthetic} issues={len(result.issues)}")
    for p in paths:
        print(f"  -> {p}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
