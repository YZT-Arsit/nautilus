from __future__ import annotations

from feature_engine.api import (
    adx_spec,
    aroon_spec,
    awesome_oscillator_spec,
    ema_spec,
    FeatureSpec,
    atr_spec,
    bollinger_percent_b_spec,
    bollinger_width_spec,
    cci_spec,
    confirmed_fractal_spec,
    hlc_mean_spec,
    hma_spec,
    rolling_mean_spec,
    rsi_spec,
    breakout_down_spec,
    breakout_up_spec,
    minus_di_spec,
    macd_spec,
    plus_di_spec,
    psar_spec,
    supertrend_spec,
    crypto_utc_session_spec,
    session_flatten_due_spec,
    completed_timeframe_spec,
    return_n_spec,
)
from strategy_framework.plugin import StrategyPlugin

from strategies.workbook_parametric.config import WorkbookParametricConfig
from strategies.workbook_parametric.strategy import WorkbookParametricStrategy


def build_specs(config: WorkbookParametricConfig) -> list[FeatureSpec]:
    common = [rolling_mean_spec("workbook_close", input_field="close", window=1)]
    if config.family.startswith("session_"):
        session = common + [
            crypto_utc_session_spec("workbook_session_vwap", output="session_vwap"),
            crypto_utc_session_spec("workbook_session_start", output="session_start_ns"),
            crypto_utc_session_spec(
                "workbook_session_entry_allowed", output="session_entry_allowed",
                execution_lag_minutes=config.execution_lag_minutes,
            ),
            session_flatten_due_spec(
                "workbook_session_flatten", execution_lag_minutes=config.execution_lag_minutes,
            ),
        ]
        if config.family == "session_vwap_ma_trend":
            return session + [
                completed_timeframe_spec(
                    "workbook_completed_ma", timeframe_minutes=5, output="sma",
                    window=config.window,
                ),
                atr_spec("workbook_atr", window=config.atr_window),
            ]
        if config.family == "session_vwap_roc_turn":
            return session + [return_n_spec("workbook_roc", window=1)]
        if config.family == "session_vwap_volume_mean":
            return session + [
                rolling_mean_spec("workbook_volume", input_field="volume", window=1),
                rolling_mean_spec(
                    "workbook_volume_mean", input_field="volume", window=config.volume_window,
                ),
            ]
        if config.family == "session_vwap_fractal":
            return session + [
                completed_timeframe_spec(
                    "workbook_upper_fractal_15m", timeframe_minutes=15,
                    output="upper_fractal_pulse",
                ),
                completed_timeframe_spec(
                    "workbook_lower_fractal_15m", timeframe_minutes=15,
                    output="lower_fractal_pulse",
                ),
                atr_spec("workbook_atr", window=config.atr_window),
            ]
        if config.family == "session_vwap_mtf_fractal":
            specs = list(session)
            for minutes in (5, 15, 30):
                specs.extend([
                    completed_timeframe_spec(
                        f"workbook_upper_fractal_{minutes}m", timeframe_minutes=minutes,
                        output="upper_fractal_pulse",
                    ),
                    completed_timeframe_spec(
                        f"workbook_lower_fractal_{minutes}m", timeframe_minutes=minutes,
                        output="lower_fractal_pulse",
                    ),
                ])
            return specs
    if config.family == "sma_crossover":
        return common + [
            rolling_mean_spec("workbook_fast", window=config.fast_window),
            rolling_mean_spec("workbook_slow", window=config.slow_window),
        ]
    if config.family == "sma_price_cross":
        return common + [rolling_mean_spec("workbook_middle", window=config.window)]
    if config.family == "ema_crossover":
        return common + [
            ema_spec("workbook_fast", window=config.fast_window),
            ema_spec("workbook_slow", window=config.slow_window),
        ]
    if config.family == "ma_envelope":
        return common + [rolling_mean_spec("workbook_middle", window=config.window)]
    if config.family == "bollinger":
        return common + [
            rolling_mean_spec("workbook_middle", window=config.window),
            bollinger_percent_b_spec("workbook_percent_b", window=config.window, k=config.multiplier),
        ]
    if config.family == "bollinger_width_cross":
        return common + [
            bollinger_width_spec("workbook_bbw_fast", window=config.fast_window),
            bollinger_width_spec("workbook_bbw_slow", window=config.slow_window),
        ]
    if config.family == "ma_cross_slope_atr_exit":
        average = ema_spec if config.average_type == "ema" else rolling_mean_spec
        return common + [
            average("workbook_fast", window=config.fast_window),
            average("workbook_slow", window=config.slow_window),
            atr_spec("workbook_atr", window=config.atr_window),
        ]
    if config.family == "rsi_turn_candle":
        return common + [
            rolling_mean_spec("workbook_open", input_field="open", window=1),
            rsi_spec("workbook_rsi", window=config.rsi_window),
        ]
    if config.family == "adx_ma_di_confluence":
        return common + [
            rolling_mean_spec("workbook_middle", window=config.window),
            adx_spec("workbook_adx", window=config.adx_window),
            plus_di_spec("workbook_plus_di", window=config.adx_window),
            minus_di_spec("workbook_minus_di", window=config.adx_window),
        ]
    if config.family == "macd_zero_trend":
        kwargs = {
            "fast_window": config.macd_fast_window,
            "slow_window": config.macd_slow_window,
            "signal_window": config.macd_signal_window,
        }
        return common + [
            macd_spec("workbook_macd_dif", output="dif", **kwargs),
            macd_spec("workbook_macd_signal", output="signal", **kwargs),
            macd_spec("workbook_macd_histogram", output="histogram", **kwargs),
        ]
    if config.family == "macd_zero_persistent":
        kwargs = {
            "fast_window": config.macd_fast_window,
            "slow_window": config.macd_slow_window,
            "signal_window": config.macd_signal_window,
        }
        return common + [
            macd_spec("workbook_macd_dif", output="dif", **kwargs),
            macd_spec("workbook_macd_signal", output="signal", **kwargs),
            macd_spec("workbook_macd_histogram", output="histogram", **kwargs),
        ]
    if config.family in {"ao_zero_persistent", "ema_ao_persistent"}:
        specs = common + [awesome_oscillator_spec(
            "workbook_ao", fast_window=config.ao_fast_window,
            slow_window=config.ao_slow_window,
        )]
        if config.family == "ema_ao_persistent":
            specs.append(ema_spec("workbook_middle", window=config.window))
        return specs
    if config.family == "four_ma_stable_layered":
        return common + [
            rolling_mean_spec("workbook_fast", window=config.fast_window),
            rolling_mean_spec("workbook_middle_ma", window=config.middle_window),
            rolling_mean_spec("workbook_slow", window=config.slow_window),
            rolling_mean_spec("workbook_filter", window=config.filter_window),
        ]
    if config.family == "triple_sma_ordered":
        return common + [
            rolling_mean_spec("workbook_fast", window=config.fast_window),
            rolling_mean_spec("workbook_middle_ma", window=config.middle_window),
            rolling_mean_spec("workbook_slow", window=config.slow_window),
        ]
    if config.family == "psar_ma_stable_reduce":
        return common + [
            rolling_mean_spec("workbook_middle", window=config.window),
            psar_spec("workbook_psar", step=config.psar_step, maximum=config.psar_maximum),
            psar_spec("workbook_psar_direction", step=config.psar_step,
                      maximum=config.psar_maximum, output="direction"),
            atr_spec("workbook_atr", window=config.atr_window),
        ]
    if config.family == "psar_atr_distance_exit":
        return common + [
            psar_spec("workbook_psar", step=config.psar_step, maximum=config.psar_maximum),
            psar_spec("workbook_psar_direction", step=config.psar_step,
                      maximum=config.psar_maximum, output="direction"),
            atr_spec("workbook_atr", window=config.atr_window),
        ]
    if config.family == "ma_rsi_turn_filter":
        return common + [
            rolling_mean_spec("workbook_middle", window=config.window),
            rsi_spec("workbook_rsi", window=config.rsi_window),
        ]
    if config.family in {"atr_channel", "atr_channel_confirmed"}:
        return common + [
            rolling_mean_spec("workbook_middle", window=config.window),
            atr_spec("workbook_atr", window=config.atr_window),
        ]
    if config.family == "triple_sma":
        return common + [
            rolling_mean_spec("workbook_fast", window=config.fast_window),
            rolling_mean_spec("workbook_middle_ma", window=config.middle_window),
            rolling_mean_spec("workbook_slow", window=config.slow_window),
        ]
    if config.family == "hma_turn":
        return common + [hma_spec("workbook_hma", window=config.window)]
    if config.family == "cci_ma":
        return common + [
            cci_spec("workbook_cci", window=config.window),
            rolling_mean_spec("workbook_middle", window=config.window),
        ]
    if config.family == "hlc_mean_cross_confirmed":
        return common + [hlc_mean_spec("workbook_hlc_mean", window=config.window)]
    if config.family in {"adx_donchian", "adx_di_donchian", "adx_di_cross_donchian", "adx_di_recent_extreme"}:
        specs = common + [
            adx_spec("workbook_adx", window=config.adx_window),
            breakout_up_spec("workbook_entry_up", window=config.window),
            breakout_down_spec("workbook_entry_down", window=config.window),
            breakout_up_spec("workbook_exit_up", window=config.exit_window),
            breakout_down_spec("workbook_exit_down", window=config.exit_window),
        ]
        if config.family in {"adx_di_donchian", "adx_di_cross_donchian", "adx_di_recent_extreme"}:
            specs.extend([
                plus_di_spec("workbook_plus_di", window=config.adx_window),
                minus_di_spec("workbook_minus_di", window=config.adx_window),
            ])
        return specs
    if config.family == "ao_breakout":
        return common + [
            awesome_oscillator_spec(
                "workbook_ao", fast_window=config.ao_fast_window,
                slow_window=config.ao_slow_window,
            ),
            breakout_up_spec("workbook_entry_up", window=config.breakout_window),
            breakout_down_spec("workbook_entry_down", window=config.breakout_window),
        ]
    if config.family in {"aroon_trend", "aroon_oscillator"}:
        return common + [
            aroon_spec("workbook_aroon_up", window=config.aroon_window, output="up"),
            aroon_spec("workbook_aroon_down", window=config.aroon_window, output="down"),
            aroon_spec("workbook_aroon_osc", window=config.aroon_window, output="oscillator"),
        ]
    if config.family == "psar_reversal":
        return common + [
            psar_spec("workbook_psar", step=config.psar_step, maximum=config.psar_maximum),
            psar_spec("workbook_psar_direction", step=config.psar_step,
                      maximum=config.psar_maximum, output="direction"),
        ]
    if config.family == "fractal_ma_breakout":
        return common + [
            rolling_mean_spec("workbook_middle", window=config.window),
            confirmed_fractal_spec("workbook_upper_fractal", output="upper"),
            confirmed_fractal_spec("workbook_lower_fractal", output="lower"),
        ]
    if config.family == "fractal_adx":
        return common + [
            rolling_mean_spec("workbook_middle", window=config.window),
            adx_spec("workbook_adx", window=config.adx_window),
            confirmed_fractal_spec("workbook_upper_pulse", output="upper_pulse"),
            confirmed_fractal_spec("workbook_lower_pulse", output="lower_pulse"),
        ]
    if config.family == "fractal_adx_stable":
        return common + [
            rolling_mean_spec("workbook_middle", window=config.window),
            adx_spec("workbook_adx", window=config.adx_window),
            confirmed_fractal_spec("workbook_upper_pulse", output="upper_pulse"),
            confirmed_fractal_spec("workbook_lower_pulse", output="lower_pulse"),
        ]
    if config.family == "cci_touch_reduce":
        return common + [
            cci_spec("workbook_cci", window=config.window),
            rolling_mean_spec("workbook_middle", window=config.window),
        ]
    if config.family == "donchian_pyramid":
        return common + [
            atr_spec("workbook_atr", window=config.atr_window),
            breakout_up_spec("workbook_trend_up", window=config.trend_window),
            breakout_down_spec("workbook_trend_down", window=config.trend_window),
            breakout_up_spec("workbook_entry_up", window=config.entry_window),
            breakout_down_spec("workbook_entry_down", window=config.entry_window),
            breakout_up_spec("workbook_exit_up", window=config.exit_window),
            breakout_down_spec("workbook_exit_down", window=config.exit_window),
        ]
    if config.family == "sma_donchian_trend":
        return common + [
            rolling_mean_spec("workbook_middle", window=config.trend_window),
            breakout_up_spec("workbook_entry_up", window=config.entry_window),
            breakout_down_spec("workbook_entry_down", window=config.entry_window),
            breakout_up_spec("workbook_exit_up", window=config.exit_window),
            breakout_down_spec("workbook_exit_down", window=config.exit_window),
        ]
    if config.family == "supertrend_stop":
        return common + [
            supertrend_spec("workbook_supertrend_direction", window=config.window,
                            multiplier=config.multiplier, output="direction"),
            atr_spec("workbook_atr", window=config.atr_window),
        ]
    if config.family == "donchian_stop":
        return common + [
            atr_spec("workbook_atr", window=config.atr_window),
            breakout_up_spec("workbook_entry_up", window=config.entry_window),
            breakout_down_spec("workbook_entry_down", window=config.entry_window),
            breakout_up_spec("workbook_exit_up", window=config.exit_window),
            breakout_down_spec("workbook_exit_down", window=config.exit_window),
        ]
    if config.family == "donchian_ma_stop":
        return common + [
            rolling_mean_spec("workbook_middle", window=config.window),
            atr_spec("workbook_atr", window=config.atr_window),
            breakout_up_spec("workbook_entry_up", window=config.entry_window),
            breakout_down_spec("workbook_entry_down", window=config.entry_window),
            breakout_up_spec("workbook_exit_up", window=config.exit_window),
            breakout_down_spec("workbook_exit_down", window=config.exit_window),
        ]
    if config.family == "adx_donchian_stop":
        return common + [
            adx_spec("workbook_adx", window=config.adx_window),
            atr_spec("workbook_atr", window=config.atr_window),
            breakout_up_spec("workbook_entry_up", window=config.entry_window),
            breakout_down_spec("workbook_entry_down", window=config.entry_window),
            breakout_up_spec("workbook_exit_up", window=config.exit_window),
            breakout_down_spec("workbook_exit_down", window=config.exit_window),
        ]
    if config.family == "adx_sma_take_profit":
        return common + [
            rolling_mean_spec("workbook_fast", window=config.fast_window),
            rolling_mean_spec("workbook_slow", window=config.slow_window),
            adx_spec("workbook_adx", window=config.adx_window),
            atr_spec("workbook_atr", window=config.atr_window),
        ]
    if config.family == "ema_adx_take_profit":
        return common + [
            ema_spec("workbook_fast", window=config.fast_window),
            ema_spec("workbook_slow", window=config.slow_window),
            adx_spec("workbook_adx", window=config.adx_window),
            plus_di_spec("workbook_plus_di", window=config.adx_window),
            minus_di_spec("workbook_minus_di", window=config.adx_window),
            atr_spec("workbook_atr", window=config.atr_window),
        ]
    raise ValueError(f"unsupported exact workbook family: {config.family}")


# This module is a reusable family implementation, not a registry.  Each
# reviewed workbook row exposes its own PLUGIN from ``strategies/xlsx_*``.
DEFINITIONS: dict[str, str] = {}
PLUGINS: tuple[StrategyPlugin, ...] = ()
