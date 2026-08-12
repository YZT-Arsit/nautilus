from results.charts import _signed_leverage_percent


def test_position_chart_uses_signed_leverage_percent() -> None:
    series = {
        "t": [1, 2, 3, 4],
        "position": [1.0, -2.0, 0.0, 3.0],
        "price": [100_000.0, 50_000.0, 80_000.0, 100_000.0],
        "leverage": [],
    }

    assert _signed_leverage_percent(series, {"initial_cash": 100_000.0}) == [
        100.0,
        -100.0,
        0.0,
        300.0,
    ]
