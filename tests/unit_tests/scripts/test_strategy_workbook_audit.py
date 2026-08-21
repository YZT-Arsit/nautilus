from pathlib import Path

from scripts.internal.audit_strategy_workbook import IMPLEMENTED, build_manifest, classify


def row(name: str, definition: str, long: str, short: str, exit: str) -> list[str]:
    return ["1", name, definition, long, short, exit]


def test_external_breadth_is_blocked_missing_data() -> None:
    result = classify(row("MSI", "上涨家数-下跌家数 EMA19/39", "MSI突破", "MSI跌破", "反向"))
    assert result["final_status"] == "blocked_missing_data"
    assert result["automatic_conversion_safe"] is False


def test_daily_and_multitimeframe_are_never_silently_flattened() -> None:
    daily = classify(row("60单均线", "近60日收盘价均值", "价格上穿均线", "价格下穿均线", "反向击穿"))
    multi = classify(row("共振", "日线 + 4H EMA20", "多周期同步", "多周期同步", "反向"))
    assert daily["final_status"] == "unsafe_timeframe_conversion"
    assert multi["final_status"] == "blocked_engine_capability"


def test_real_workbook_has_no_unaccounted_rows_or_identity_collision() -> None:
    manifest, counts = build_manifest(Path("时序策略.xlsx"))
    assert counts == {"Sheet1": 815, "Sheet2": 900}
    assert len(manifest) == 1715
    assert len({row["registry_id"] for row in manifest}) == 1715
    assert sum(row["final_status"] == "implemented" for row in manifest) == len(IMPLEMENTED)
    assert all(row["final_status"] for row in manifest)
