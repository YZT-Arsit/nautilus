# Market Type Support Matrix

D1b adds a market integration layer above the D1a raw data adapter layer.

The two layers are intentionally distinct:

- Market type / asset class determines metadata, sessions, fee assumptions,
  tradability, and instrument mapping requirements.
- Raw data type determines how files or events are adapted into canonical bars
  or instrument metadata.

## Layer 1: Market Type / Asset Class Support Matrix

| market_type | asset_class | examples | raw_data_available | metadata_available | canonical_output | vwm_compatibility | current_status | caveat | next_action |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| crypto_spot | crypto_spot | BTCUSDT.BINANCE, ETHUSDT.BINANCE | ohlcv_bar, aggTrades, trade_tick, order_book_depth | base_asset, quote_asset, price_precision, size_precision, fee_model, 24/7_session | canonical_trade_bar, canonical_instrument_metadata | true_if_trade_ohlcv_bar_exists | implemented / validated with BTCUSDT 1m and 5m smoke | aggTrades coverage must be checked before expanding trade-derived bars | add coverage-aware aggTrades/trade tick to OHLCV smoke |
| crypto_futures_or_perp | crypto_derivative | BTCUSDT perpetual, ETHUSDT perpetual | ohlcv_bar, trade_tick, funding_rate, mark_price, index_price | contract_type, multiplier, margin_currency, funding_schedule, fee_model | canonical_trade_bar, canonical_instrument_metadata, funding_metadata_future | true_for_trade_bars_caveated_if_funding_ignored | planned unless existing data is inventoried | funding, mark price, and margin effects are not represented in simple spot-style bars | inventory available perp bars and funding/mark metadata |
| equity_index_futures | futures | IF2303.CFFEX, IH2303.CFFEX, IC2303.CFFEX, IM2303.CFFEX | quote_tick, order_book_depth, futures_contract, trade_tick_if_available | multiplier, tick_size, lot_size, currency, expiry, exchange, trading_session, margin_model, fee_model | canonical_mid_bar, canonical_trade_bar_if_trade_ticks_available, canonical_instrument_metadata | smoke_only_for_quote_mid_true_only_for_real_trade_ohlcv | minimum pipeline validated with quote-mid derived bars and deterministic MVP mapping | CFFEX quote-mid bars are not real trade OHLCV; volume is quote update count; strategy results are pipeline-smoke evidence only | replace deterministic MVP mapping with catalog-backed futures_contract metadata |
| equities | single_name_equity | A-share stock, US stock, single-name equity | adjusted_ohlcv_bar, raw_ohlcv_bar, trade_tick, corporate_actions | exchange, currency, lot_size, tick_size, trading_calendar, corporate_action_adjustment, suspension_status, limit_up_limit_down_rules | canonical_trade_bar, canonical_adjusted_bar, canonical_instrument_metadata | true_if_adjusted_or_trade_ohlcv_bar_exists | planned | corporate actions, suspensions, and limit rules must be handled before claiming comparability | define adjusted-bar policy and A-share/US-equity metadata requirements |
| etfs | fund | index ETF, sector ETF | adjusted_ohlcv_bar, nav_optional, constituents_optional | exchange, currency, lot_size, tick_size, adjustment, fee_model | canonical_trade_bar, canonical_adjusted_bar, canonical_instrument_metadata | true_if_adjusted_ohlcv_bar_exists | planned | NAV and constituent data are optional for VWM but required for richer ETF analysis | inventory ETF adjusted bars and metadata availability |
| indices | index | CSI300, S&P500, futures underlying index | index_ohlc | currency, index_provider, session, non_tradable_marker | canonical_index_bar | analysis_only | planned | indices are non-tradable unless mapped to a futures, ETF, or other tradable proxy | add non-tradable marker and proxy mapping policy |
| options | option | option chain, index option, single-name option | option_chain, trade_tick, quote_tick, greeks, implied_vol_surface | underlying, strike, expiry, option_type, multiplier, exercise_style | option_chain_frame, option_quote_frame, option_metadata | not_directly_compatible | future_work | requires option-specific strategy and risk model; VWM bar strategy is not directly applicable | plan option-specific data and strategy interface separately |

## Layer 2: Raw Data Type Adapter Matrix

| raw_data_type | adapter | output_type | trade_bar_or_mid_bar | confidence | caveat | used_by_market_types |
| --- | --- | --- | --- | --- | --- | --- |
| ohlcv_bar | direct_bar_adapter | canonical_trade_bar | trade_bar | high | real traded OHLCV | crypto_spot, crypto_futures_or_perp |
| aggTrades | aggtrades_to_ohlcv_bar | canonical_trade_bar | trade_bar | high | real traded OHLCV after aggregation; coverage still date-dependent | crypto_spot |
| trade_tick | trades_to_ohlcv_bar | canonical_trade_bar | trade_bar | high | real traded OHLCV after aggregation; coverage still date-dependent | crypto_spot, crypto_futures_or_perp, equities, options |
| quote_tick | quote_tick_to_mid_bar | canonical_mid_bar | mid_bar | medium | derived mid-price bar; not trade OHLCV; volume is quote update count or zero | equity_index_futures, options |
| order_book_depth | depth_to_mid_bar | canonical_mid_bar | mid_bar | medium | derived top-of-book mid-price bar; not trade OHLCV; can also produce depth features | crypto_spot, equity_index_futures |
| futures_contract | contract_to_instrument_metadata | canonical_instrument_metadata | metadata | high if native_catalog, medium if deterministic_mvp | metadata only; deterministic_mvp source must remain marked until catalog-backed | equity_index_futures |

## Caveat

CFFEX bar 是 quote-mid 派生 bar，不是真实成交 OHLCV。volume 是 quote 更新次数，
不是成交量。因此这些回测结果只能说明数据类型接入链路走通，不能作为策略收益有效性的证据。
