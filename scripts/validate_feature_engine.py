#!/usr/bin/env python3
"""feature_engine 最小验证（需要 polars；缺失时清晰跳过，退出 0）。

验证项：
1. synthetic 2 symbols × 2 trading dates × 1m bars 可构建（用 data_engine 纯路径）。
2. BatchEngine 与 StreamingEngine 输出一致（逐特征 compute_batch == concat(update(chunk))）。
3. 至少覆盖 sma_20 / rsi_14 / macd / vol_30 / vwm_20 / vwm_zscore_60，误差 <= 1e-6。
4. HistoricalFeatureBuilder.build_from_dataframe 可在该 DataFrame 上产出特征列。

设计：聚合用 ``data_engine.transforms``（纯标准库），特征计算与一致性用
feature_engine 既有原语（与 test_streaming_batch_parity 同一契约）。

用法：``python scripts/validate_feature_engine.py``
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

_FEATURES = ["sma_20", "rsi_14", "macd", "vol_30", "vwm_20", "vwm_zscore_60"]
ONE_S = 1_000_000_000


def _synthetic_bars():
    """2 symbols × 2 trading dates × 1m bars（确定性游走），返回 BarEvent 列表。"""
    from data_engine.transforms import aggregate_ticks_to_bars

    bars = []
    base_dates = ("2026-05-26", "2026-05-27")
    for s, sym in enumerate(("AAA.X", "BBB.X")):
        for d, date in enumerate(base_dates):
            day_offset = (s * 2 + d) * 24 * 3600 * ONE_S
            ticks = []
            for i in range(180):  # 180s -> 3 根 1m bar
                price = 100.0 + s * 10 + math.sin(i / 10.0) + i * 0.01
                ticks.append({
                    "instrument_id": sym,
                    "event_time_ns": day_offset + i * ONE_S,
                    "price": price,
                    "size": 1.0,
                })
            res = aggregate_ticks_to_bars(ticks, frequency="1m", trading_date=date)
            bars.extend(res.bars)
    return bars


def _chunks(df, size):
    for i in range(0, df.height, size):
        yield df.slice(i, size)


def main() -> int:
    try:
        import polars as pl  # noqa: F401
    except ImportError:
        print("[SKIP] feature_engine 验证需要 polars，本环境未安装 -> 跳过（退出 0）。")
        print("       请在已安装 polars 的环境（如远端服务器）运行本脚本。")
        return 0

    from data_engine.adapters import bars_to_polars
    from feature_engine.core import registry as _registry
    from feature_engine.features import load_all
    from feature_engine.services import HistoricalFeatureBuilder

    load_all()
    df = bars_to_polars(_synthetic_bars()).sort(["symbol", "ts_event"])
    print(f"== feature_engine 最小验证 ==  输入 {df.height} 行（2 symbols × 2 dates × 1m）")

    failed = 0
    for name in _FEATURES:
        cls = _registry.get(name)
        expected = cls().compute_batch(df)
        streamer = cls()
        pieces = [streamer.update(chunk) for chunk in _chunks(df, 7)]
        actual = pl.concat(pieces, how="vertical")
        ok = _approx_equal(expected, actual, tol=1e-6)
        print(f"[{'PASS' if ok else 'FAIL'}] parity {name}")
        failed += 0 if ok else 1

    enriched = HistoricalFeatureBuilder(_FEATURES).build_from_dataframe(df)
    new_cols = [c for c in enriched.columns if c not in df.columns]
    has_all = all(any(c.startswith(f.split("_")[0]) or c == f for c in new_cols) for f in ["sma_20", "rsi_14", "vwm_20"])
    print(f"[{'PASS' if new_cols else 'FAIL'}] HistoricalFeatureBuilder 产出特征列: {new_cols}")
    failed += 0 if (new_cols and has_all) else 1

    print("== 完成 ==" if failed == 0 else f"== 失败 {failed} 项 ==")
    return 1 if failed else 0


def _approx_equal(a, b, tol: float) -> bool:
    if a.shape != b.shape or list(a.columns) != list(b.columns):
        return False
    for col in a.columns:
        for x, y in zip(a[col].to_list(), b[col].to_list()):
            if x is None and y is None:
                continue
            if x is None or y is None:
                return False
            if isinstance(x, float) and (math.isnan(x) or math.isnan(y)):
                if math.isnan(x) and math.isnan(y):
                    continue
                return False
            if isinstance(x, (int, float)) and abs(x - y) > tol:
                return False
    return True


if __name__ == "__main__":
    raise SystemExit(main())
