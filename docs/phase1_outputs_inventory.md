# Phase 1 Outputs Inventory

Non-destructive classification of `outputs/backtests/`. Cleanup only *moves* `archive_*` dirs to an archive root; `keep_*` and `manual_review` are never touched.

Total directories: 31

## keep_delivery (2)

| dir | experiment_type | summary | eval_table | equity_curve | jobs |
| --- | --- | --- | --- | --- | --- |
| vwm_crypto_perpetual_2026q2_15m_vol_targeted | vol_targeted_batch (recommended) | yes | yes | yes | 4 |
| vwm_crypto_perpetual_2026q2_sizing_comparison | sizing_mode_comparison | no | yes | no | 0 |

## keep_reference (10)

| dir | experiment_type | summary | eval_table | equity_curve | jobs |
| --- | --- | --- | --- | --- | --- |
| vwm_btcusdt_perpetual_5m_eval | 2024_single_eval (dev validation) | yes | yes | yes | 1 |
| vwm_btcusdt_perpetual_matrix | 2024_matrix_aggregate | no | yes | no | 0 |
| vwm_btcusdt_perpetual_matrix_w30d | 2024_matrix_window_source | yes | no | yes | 3 |
| vwm_btcusdt_perpetual_matrix_w7d | 2024_matrix_window_source | yes | no | yes | 3 |
| vwm_btcusdt_perpetual_matrix_w90d | 2024_matrix_window_source | yes | no | yes | 3 |
| vwm_crypto_perpetual_2026q2_15m_batch | fixed_quantity_batch | yes | yes | yes | 4 |
| vwm_crypto_perpetual_2026q2_15m_notional_normalized | notional_normalized_batch | yes | yes | yes | 4 |
| vwm_crypto_perpetual_2026q2_15m_vol_targeted_trend_filtered | trend_filtered_batch | yes | yes | yes | 4 |
| vwm_crypto_perpetual_2026q2_trend_filter_comparison | trend_filter_comparison | no | yes | no | 0 |
| vwm_strategy_batch_eval | phase4_pivot (old rows=metric orientation) | no | no | no | 0 |

## archive_superseded (3)

| dir | experiment_type | summary | eval_table | equity_curve | jobs |
| --- | --- | --- | --- | --- | --- |
| vwm_btcusdt_perpetual_matrix_30d | stale_matrix_runner_bug | yes | no | yes | 3 |
| vwm_btcusdt_perpetual_matrix_7d | stale_matrix_runner_bug | yes | no | yes | 3 |
| vwm_btcusdt_perpetual_matrix_90d | stale_matrix_runner_bug | yes | no | yes | 3 |

## manual_review (16)

| dir | experiment_type | summary | eval_table | equity_curve | jobs |
| --- | --- | --- | --- | --- | --- |
| cffex_vwm_midbar_smoke | other_track | yes | no | no | 1 |
| cffex_vwm_midbar_smoke_c2d | other_track | yes | no | yes | 4 |
| cffex_vwm_midbar_smoke_mapped | other_track | yes | no | yes | 1 |
| cffex_vwm_midbar_smoke_mapped_2 | other_track | yes | no | yes | 1 |
| crypto_perpetual_multisymbol_vwm_smoke | other_track | yes | no | yes | 4 |
| crypto_perpetual_vwm_smoke | other_track | yes | no | yes | 2 |
| ma_crossover_nautilus_synthetic | other_track | no | no | no | 0 |
| trend_breakout_atr_btcusdt_1m_20260610_20260616 | other_track | no | no | no | 0 |
| trend_breakout_atr_btcusdt_1m_20260614_20260616 | other_track | no | no | no | 0 |
| trend_breakout_atr_btcusdt_1m_20260614_20260616_nextbar | other_track | no | no | no | 0 |
| vwm_batch_smoke | other_track | no | no | yes | 2 |
| vwm_batch_smoke_isolated | other_track | yes | no | yes | 2 |
| vwm_short_binance_vision_nautilus | other_track | no | no | no | 0 |
| vwm_short_btcusdt_1m_20260610_20260616 | other_track | no | no | no | 0 |
| vwm_short_btcusdt_1m_20260614_20260616 | other_track | no | no | no | 0 |
| vwm_short_btcusdt_1m_20260614_20260616_prefee | other_track | no | no | no | 0 |

