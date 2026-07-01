#!/usr/bin/env python3
"""data_engine 最小验证（无 Nautilus、核心路径无 polars/pyarrow 也能跑）。

验证项：
1. synthetic bars 可生成。
2. csv bars 可读取（标准库 csv）。
3. BarEvent <-> Polars DataFrame round-trip（缺 polars 时**跳过**并提示）。
4. tick -> 分钟线聚合可用，OHLC 合法、合成量被标记。
5. hive parquet market_data 可读取（缺 pyarrow 时**跳过**并提示）。

退出码：核心项（1/2/4）失败 -> 非零；可选项（3/5）缺依赖只跳过，不算失败。
用法：``python scripts/validate_data_engine.py``
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

# 允许直接 `python scripts/validate_data_engine.py`。
_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from data_engine import BarEvent, load_events, make_bars  # noqa: E402
from data_engine.transforms import aggregate_ticks_to_bars  # noqa: E402

ONE_S = 1_000_000_000
_PASS, _SKIP, _FAIL = "PASS", "SKIP", "FAIL"


def _check_synthetic() -> str:
    warmup, live = load_events({"mode": "synthetic", "warmup_bars": 10, "live_bars": 10})
    assert len(warmup) == 10 and len(list(live)) == 10
    return _PASS


def _check_csv() -> str:
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "bars.csv"
        p.write_text("event_time_ns,open,high,low,close,volume\n0,9,11,8,10,5\n1000000000,10,12,9,11,6\n")
        warmup, live = load_events({"mode": "csv_bars", "path": str(p), "warmup_bars": 1})
        bars = list(live)
        assert len(warmup) == 1 and len(bars) == 1 and bars[0].close == 11.0
    return _PASS


def _check_roundtrip() -> str:
    try:
        from data_engine.adapters import bars_to_polars, polars_to_bars
        bars = make_bars([100.0, 101.0, 102.0], instrument_id="BTC/USDT")
        back = polars_to_bars(bars_to_polars(bars))
        assert [b.close for b in back] == [100.0, 101.0, 102.0]
        assert all(isinstance(b, BarEvent) for b in back)
    except ImportError:
        return _SKIP  # 无 polars：可选路径
    return _PASS


def _check_minute_bars() -> str:
    ticks = [
        {"instrument_id": "IH2303.CFFEX", "event_time_ns": i * ONE_S,
         "price": 100.0 + (i % 10) * 0.1, "size": 2.0}
        for i in range(120)
    ]
    res = aggregate_ticks_to_bars(ticks, frequency="1m")
    assert len(res.bars) == 2, f"expected 2 one-minute bars, got {len(res.bars)}"
    assert not res.issues, f"validation issues: {res.issues}"
    assert not res.volume_is_synthetic  # 有真实 size
    # 合成量路径
    res2 = aggregate_ticks_to_bars(
        [{"instrument_id": "X", "event_time_ns": 0, "price": 10.0}], frequency="1m"
    )
    assert res2.volume_is_synthetic
    return _PASS


def _check_hive_parquet() -> str:
    try:
        import pyarrow  # noqa: F401
    except ImportError:
        return _SKIP
    from feature_engine.services import MinuteBarBuilder
    from feature_engine.storage.market_reader import MarketDataReader

    ticks = [
        {"instrument_id": "IH2303.CFFEX", "event_time_ns": i * ONE_S, "price": 100.0 + i, "size": 1.0}
        for i in range(120)
    ]
    with tempfile.TemporaryDirectory() as d:
        root = Path(d) / "market_data"
        builder = MinuteBarBuilder(asset_class="future", exchange="CFFEX", venue_type="futures")
        result, paths = builder.build_and_write(
            ticks, instrument_id="IH2303.CFFEX", market_root=root,
            frequency="1m", trading_date="2026-05-26",
        )
        assert paths, "no parquet written"
        df = MarketDataReader(root).scan(freq="1m", date="2026-05-26",
                                         venue_type="futures", data_type="bar",
                                         symbol="IH2303.CFFEX")
        assert df.height == len(result.bars)
    return _PASS


def main() -> int:
    checks = [
        ("1. synthetic bars", _check_synthetic),
        ("2. csv bars", _check_csv),
        ("3. BarEvent<->DataFrame round-trip", _check_roundtrip),
        ("4. tick -> 分钟线", _check_minute_bars),
        ("5. hive parquet market_data", _check_hive_parquet),
    ]
    optional = {"3. BarEvent<->DataFrame round-trip", "5. hive parquet market_data"}
    failed = 0
    print("== data_engine 最小验证 ==")
    for label, fn in checks:
        try:
            status = fn()
        except Exception as exc:  # noqa: BLE001
            status = _FAIL
            print(f"[{_FAIL}] {label}: {type(exc).__name__}: {exc}")
            if label not in optional:
                failed += 1
            continue
        note = "（缺可选依赖，跳过）" if status == _SKIP else ""
        print(f"[{status}] {label} {note}")
    print("== 完成 ==" if failed == 0 else f"== 失败 {failed} 项 ==")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
