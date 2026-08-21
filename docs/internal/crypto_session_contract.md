# Crypto UTC Session Contract

`CRYPTO_UTC_SESSION_V1` is a project research convention for continuously
traded BTCUSDT data. It is not a Binance exchange open/close.

- Timezone: UTC.
- Session: `[00:00:00 UTC, next 00:00:00 UTC)`.
- Session ID: the UTC calendar date containing the event/bar observation.
- Previous session: only the immediately preceding fully completed UTC day.
- Session open: the first real observation in the UTC day; missing midnight
  observations are not synthesized.
- Session high/low and VWAP: cumulative through the observations available at
  the decision timestamp. VWAP uses source quote volume when present and an
  explicitly counted `close * volume` fallback only when that bar lacks it.
- Opening range: unavailable until its complete source-defined interval ends.
- Daily bars: visible to lower timeframes only after completion.
- Week: Monday 00:00 UTC to next Monday 00:00 UTC.
- Month: UTC calendar month.
- Session flatten: a flat target is emitted early enough that the configured
  execution lag fills on the last real executable observation before midnight.
  New entries remain disabled from that flatten decision through the boundary,
  so a lagged re-entry cannot fill at or after midnight. No synthetic 24:00
  fill is permitted.
- UTC has no daylight-saving transition; every baseline session is 24 hours.

Traditional closed-market overnight gaps and opening/closing auctions are not
created at UTC midnight. Strategies whose economics require those mechanisms
remain explicitly blocked unless corresponding source data and contracts exist.
