from scripts.internal.audit_strategy_workbook import REGISTERED, classify


def row(name: str, definition: str, long: str, short: str, exit: str) -> list[str]:
    return ["1", name, definition, long, short, exit]


def test_external_breadth_is_not_misclassified_as_convertible_ema() -> None:
    result = classify(row("MSI", "上涨家数-下跌家数 EMA19/39", "MSI突破", "MSI跌破", "反向"))
    assert result["classification"] == "F_EXTERNAL_OR_CROSS_SECTIONAL_DATA"
    assert result["automatic_conversion_safe"] is False


def test_daily_and_multitimeframe_semantics_require_review() -> None:
    daily = classify(row("60单均线", "近60日收盘价均值", "价格上穿均线", "价格下穿均线", "反向击穿"))
    multi = classify(row("共振", "日线 + 4H EMA20", "多周期同步", "多周期同步", "反向"))
    assert daily["classification"] == "C_DAILY_TO_INTRADAY_PARAMETRIC"
    assert multi["classification"] == "D_MULTI_TIMEFRAME"


def test_first_batch_registry_ids_are_collision_safe() -> None:
    assert len(REGISTERED) == 4
    assert all(value.startswith("xlsx_s") for value in REGISTERED)
