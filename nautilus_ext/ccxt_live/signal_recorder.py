"""
SignalRecorder — append-only log of per-bar signal engine output.

One row per bar processed by CcxtPaperLiveRunner.  Columns:
    ts_event            int         millisecond POSIX timestamp of the bar
    datetime            str         ISO-8601 UTC string
    instrument_id       str         Nautilus InstrumentId string
    bar_type            str         Nautilus BarType string
    open / high / low / close / volume   float
    current_bar         int         bar counter from the signal engine
    momentum            float|None
    vwm                 float|None
    atr                 float|None
    bull_setup          bool
    bear_setup          bool
    se_price            float|None  setup-entry reference price
    s_setup             int         bars since last bear setup
    entry_signal        bool
    exit_signal         bool
    entry_setup_active  bool
    entry_trigger_price float|None
    reason              str|None    "enter_short" | "exit_short" | None
    position            int         -1 / 0 / 1 after processing this bar

Supports CSV and Parquet export.
"""
from __future__ import annotations

import logging
from pathlib import Path
import json

import pandas as pd

log = logging.getLogger(__name__)

_COLUMNS = [
    "ts_event",
    "datetime",
    "event_type",
    "instrument_id",
    "bar_type",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "current_bar",
    "momentum",
    "vwm",
    "atr",
    "bull_setup",
    "bear_setup",
    "se_price",
    "s_setup",
    "entry_signal",
    "exit_signal",
    "entry_setup_active",
    "entry_trigger_price",
    "reason",
    "signal_name",
    "order_intents_count",
    "debug_json",
    "state_json",
    "position",
    # Feature references — point back to the FeatureStore.
    # Authoritative features live in features/offline/…parquet, not here.
    "feature_set_ids",   # debug: comma-separated feature set IDs used this bar
    "feature_event_ts",  # debug: ts_event of the most recent FeatureEvent
]


class SignalRecorder:
    """Collects per-bar signal rows and exports them to CSV / Parquet.

    Parameters
    ----------
    instrument_id : str
        Nautilus InstrumentId string, e.g. "BTCUSDT-PERP.BINANCE".
    bar_type : str
        Nautilus BarType string, e.g. "BTCUSDT-PERP.BINANCE-1-MINUTE-LAST-EXTERNAL".
    """

    def __init__(self, instrument_id: str, bar_type: str) -> None:
        self._instrument_id = instrument_id
        self._bar_type = bar_type
        self._rows: list[dict] = []

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def append(
        self,
        ohlcv_row: "pd.Series",
        result,
        position: int,
        feature_refs: dict | None = None,
    ) -> None:
        """Record one bar.

        Parameters
        ----------
        ohlcv_row : pd.Series
            A row from the OHLCV DataFrame produced by CcxtPollingBarFeed.
            Must have: timestamp_ms, open, high, low, close, volume, datetime.
        result : SignalResult
            Output of signal_engine.update().  result.debug contains the
            feature snapshot fields.
        position : int
            Current position AFTER processing this bar: -1 / 0 / 1.
        feature_refs : dict | None
            Optional back-reference to the FeaturePipeline output.
            Keys: ``feature_set_ids`` (str), ``feature_event_ts`` (int | None).
            The authoritative features live in the OfflineFeatureStore parquet
            files; these columns are for debugging and cross-referencing only.
        """
        debug = result.debug or {}
        state = result.state or {}
        order_intents = getattr(result, "order_intents", []) or []
        fr = feature_refs or {}
        ts_ms = int(ohlcv_row["timestamp_ms"])
        dt_val = ohlcv_row.get("datetime")
        if hasattr(dt_val, "isoformat"):
            dt_str = dt_val.isoformat()
        else:
            dt_str = str(dt_val)

        self._rows.append({
            "ts_event":            ts_ms,
            "datetime":            dt_str,
            "event_type":          "bar",
            "instrument_id":       self._instrument_id,
            "bar_type":            self._bar_type,
            "open":                float(ohlcv_row["open"]),
            "high":                float(ohlcv_row["high"]),
            "low":                 float(ohlcv_row["low"]),
            "close":               float(ohlcv_row["close"]),
            "volume":              float(ohlcv_row["volume"]),
            "current_bar":         debug.get("current_bar"),
            "momentum":            debug.get("momentum"),
            "vwm":                 debug.get("vwm"),
            "atr":                 debug.get("atr"),
            "bull_setup":          debug.get("bull_setup", False),
            "bear_setup":          debug.get("bear_setup", False),
            "se_price":            debug.get("se_price"),
            "s_setup":             debug.get("s_setup"),
            "entry_signal":        debug.get("entry_signal", False),
            "exit_signal":         debug.get("exit_signal", False),
            "entry_setup_active":  debug.get("entry_setup_active", False),
            "entry_trigger_price": debug.get("entry_trigger_price"),
            "reason":              result.reason,
            "signal_name":         result.signal_name,
            "order_intents_count":  len(order_intents),
            "debug_json":          json.dumps(debug, ensure_ascii=True, default=str),
            "state_json":          json.dumps(state, ensure_ascii=True, default=str),
            "position":            position,
            "feature_set_ids":     fr.get("feature_set_ids"),
            "feature_event_ts":    fr.get("feature_event_ts"),
        })

    def flush(self) -> None:
        """No-op for in-memory recorder; included for interface symmetry."""

    def close(self) -> None:
        """No-op for in-memory recorder."""

    def to_dataframe(self) -> pd.DataFrame:
        if not self._rows:
            return pd.DataFrame(columns=_COLUMNS)
        return pd.DataFrame(self._rows, columns=_COLUMNS)

    def to_csv(self, path: str | Path) -> Path:
        dest = Path(path)
        dest.parent.mkdir(parents=True, exist_ok=True)
        df = self.to_dataframe()
        df.to_csv(dest, index=False)
        log.info("SignalRecorder: saved %d rows → %s", len(df), dest)
        return dest

    def to_parquet(self, path: str | Path) -> Path:
        dest = Path(path)
        dest.parent.mkdir(parents=True, exist_ok=True)
        df = self.to_dataframe()
        df.to_parquet(dest, index=False, engine="pyarrow")
        log.info("SignalRecorder: saved %d rows → %s", len(df), dest)
        return dest

    def __len__(self) -> int:
        return len(self._rows)
