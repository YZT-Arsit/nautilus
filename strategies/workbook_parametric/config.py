from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class WorkbookParametricConfig:
    source_registry_id: str = ""
    family: str = "sma_crossover"
    semantic_provenance: str = "SOURCE_EXACT"
    contracts_applied: str = ""
    defaulted_parameters: str = ""
    session_contract: str = ""
    session_contract_version: int = 0
    session_semantic_provenance: str = ""
    session_defaulted_parameters: str = ""
    contract_versions: str = ""
    modelled_interpretations: str = ""
    rule_spec_b64: str = ""
    execution_lag_minutes: int = 0
    average_type: str = "sma"
    fast_window: int = 20
    middle_window: int = 10
    slow_window: int = 60
    window: int = 20
    exit_window: int = 20
    filter_window: int = 55
    atr_window: int = 20
    multiplier: float = 1.5
    envelope_fraction: float = 0.02
    maximum_holding_bars: int = 0
    consecutive_bars: int = 1
    adx_window: int = 14
    adx_entry_threshold: float = 25.0
    adx_exit_threshold: float = 20.0
    ao_fast_window: int = 5
    ao_slow_window: int = 34
    breakout_window: int = 20
    aroon_window: int = 25
    rsi_window: int = 14
    volume_window: int = 15
    lower_threshold: float = 20.0
    upper_threshold: float = 80.0
    neutral_threshold: float = 50.0
    exit_lower_threshold: float = 30.0
    exit_upper_threshold: float = 70.0
    macd_fast_window: int = 12
    macd_slow_window: int = 26
    macd_signal_window: int = 9
    psar_step: float = 0.02
    psar_maximum: float = 0.2
    entry_window: int = 20
    trend_window: int = 55
    stop_multiple: float = 2.0
    take_profit_multiple: float = 0.0
    reduction_fraction: float = 0.5
    entry_distance_multiple: float = 0.8
    grid_layers: int = 4
    layer_fraction: float = 0.25
    pyramid_direction: str = "favorable"
    instrument_id: str = "BTCUSDT-PERP.BINANCE"
    bar_type: str | None = None

    def __post_init__(self) -> None:
        if self.family not in {
            "sma_crossover",
            "ma_envelope",
            "bollinger",
            "atr_channel",
            "atr_channel_confirmed",
            "triple_sma",
            "hma_turn",
            "cci_ma",
            "hlc_mean_cross_confirmed",
            "adx_donchian",
            "adx_di_donchian",
            "ao_breakout",
            "aroon_trend",
            "aroon_oscillator",
            "sma_price_cross",
            "ema_crossover",
            "psar_reversal",
            "fractal_ma_breakout",
            "adx_di_cross_donchian",
            "fractal_adx",
            "sma_donchian_trend",
            "supertrend_stop",
            "donchian_stop",
            "donchian_ma_stop",
            "adx_donchian_stop",
            "adx_sma_take_profit",
            "ema_adx_take_profit",
            "bollinger_width_cross",
            "ma_cross_slope_atr_exit",
            "rsi_turn_candle",
            "adx_ma_di_confluence",
            "macd_zero_trend",
            "four_ma_stable_layered",
            "psar_ma_stable_reduce",
            "psar_atr_distance_exit",
            "ma_rsi_turn_filter",
            "macd_zero_persistent",
            "ao_zero_persistent",
            "ema_ao_persistent",
            "adx_di_recent_extreme",
            "triple_sma_ordered",
            "fractal_adx_stable",
            "cci_touch_reduce",
            "donchian_pyramid",
            "session_vwap_ma_trend",
            "session_vwap_roc_turn",
            "session_vwap_volume_mean",
            "session_vwap_fractal",
            "session_vwap_mtf_fractal",
            "phase5a_declarative",
            "phase5b_declarative",
        }:
            raise ValueError(f"unsupported exact workbook family: {self.family}")
        if min(self.fast_window, self.middle_window, self.slow_window, self.window, self.entry_window, self.trend_window, self.exit_window, self.filter_window, self.atr_window, self.adx_window, self.ao_fast_window, self.ao_slow_window, self.breakout_window, self.aroon_window, self.rsi_window, self.macd_fast_window, self.macd_slow_window, self.macd_signal_window) <= 0:
            raise ValueError("all windows must be positive")
        if self.ao_fast_window >= self.ao_slow_window:
            raise ValueError("ao_fast_window must be less than ao_slow_window")
        if self.macd_fast_window >= self.macd_slow_window:
            raise ValueError("macd_fast_window must be less than macd_slow_window")
        if not 0 < self.psar_step <= self.psar_maximum:
            raise ValueError("PSAR requires 0 < step <= maximum")
        if self.stop_multiple < 0 or self.take_profit_multiple < 0:
            raise ValueError("stop_multiple and take_profit_multiple must be non-negative")
        if self.family in {
            "supertrend_stop", "donchian_stop", "donchian_ma_stop", "adx_donchian_stop",
        } and self.stop_multiple <= 0:
            raise ValueError("fill-anchored stop families require positive stop_multiple")
        if self.average_type not in {"sma", "ema"}:
            raise ValueError("average_type must be sma or ema")
        if not 0 < self.reduction_fraction <= 1:
            raise ValueError("reduction_fraction must be in (0, 1]")
        if self.entry_distance_multiple <= 0:
            raise ValueError("entry_distance_multiple must be positive")
        if self.grid_layers <= 0 or not 0 < self.layer_fraction <= 1:
            raise ValueError("grid layers and layer fraction must be positive")
        if self.pyramid_direction not in {"favorable", "adverse"}:
            raise ValueError("pyramid_direction must be favorable or adverse")
        if self.family == "sma_crossover" and self.fast_window >= self.slow_window:
            raise ValueError("fast_window must be less than slow_window")
        if self.family == "triple_sma" and not self.fast_window < self.middle_window < self.slow_window:
            raise ValueError("triple_sma requires fast_window < middle_window < slow_window")
        if self.multiplier <= 0 or self.envelope_fraction <= 0 or self.consecutive_bars <= 0:
            raise ValueError("multipliers and counts must be positive")
        if self.adx_entry_threshold < self.adx_exit_threshold or self.adx_exit_threshold < 0:
            raise ValueError("ADX thresholds must satisfy entry >= exit >= 0")
        if self.semantic_provenance not in {
            "SOURCE_EXACT", "STANDARD_CONTRACT_RESOLVED", "PARAMETER_DEFAULTED",
            "SESSION_CONTRACT_RESOLVED",
            "MODELLED_BASELINE_INTERPRETATION",
        }:
            raise ValueError("unsupported semantic provenance")
        if self.family == "phase5a_declarative" and not self.rule_spec_b64:
            raise ValueError("phase5a_declarative requires a frozen typed rule spec")
        if self.family == "phase5b_declarative" and not self.rule_spec_b64:
            raise ValueError("phase5b_declarative requires a frozen typed rule spec")
        if self.execution_lag_minutes < 0 or self.volume_window <= 0:
            raise ValueError("execution lag must be non-negative and volume window positive")
        if self.session_contract and self.session_contract != "CRYPTO_UTC_SESSION_V1":
            raise ValueError("unsupported session contract")
        if not self.lower_threshold < self.neutral_threshold < self.upper_threshold:
            raise ValueError("indicator thresholds must satisfy lower < neutral < upper")
        if not self.exit_lower_threshold < self.exit_upper_threshold:
            raise ValueError("exit thresholds must satisfy lower < upper")
