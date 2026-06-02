"""
DryRunExecutionRecorder — records order intent without real submission.

When the signal engine emits an entry or exit signal during paper live,
this recorder logs the intended order for audit purposes.  No order is ever
submitted to any exchange or broker.

Columns
-------
    ts_event        int     millisecond POSIX timestamp of the triggering bar
    datetime        str     ISO-8601 UTC string
    instrument_id   str     Nautilus InstrumentId string
    side            str     "SELL" (entry short) | "BUY" (exit short)
    order_type      str     "stop_market" | "market"
    trigger_price   float|None
    quantity        float   from config.trade_size (notional)
    reason          str|None
    status          str     always "dry_run_intent"
"""
from __future__ import annotations

import logging
from pathlib import Path

import pandas as pd

log = logging.getLogger(__name__)

_COLUMNS = [
    "ts_event",
    "datetime",
    "instrument_id",
    "side",
    "order_type",
    "trigger_price",
    "quantity",
    "reason",
    "status",
]

_STATUS = "dry_run_intent"


class DryRunExecutionRecorder:
    """Records paper-live order intents; never submits a real order.

    Parameters
    ----------
    instrument_id : str
        Nautilus InstrumentId string, e.g. "BTCUSDT-PERP.BINANCE".
    trade_size : float
        Notional quantity to record with each intent (default 1.0).
    """

    def __init__(self, instrument_id: str, trade_size: float = 1.0) -> None:
        self._instrument_id = instrument_id
        self._trade_size = trade_size
        self._rows: list[dict] = []

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def append(self, ohlcv_row: "pd.Series", result) -> None:
        """Record an order intent triggered by a signal.

        Called when result.entry_side or result.exit_side is set.
        This method NEVER submits a real order.

        Parameters
        ----------
        ohlcv_row : pd.Series
            The bar that triggered the signal.
        result : SignalResult
            Signal engine output with entry/exit fields.
        """
        ts_ms = int(ohlcv_row["timestamp_ms"])
        dt_val = ohlcv_row.get("datetime")
        if hasattr(dt_val, "isoformat"):
            dt_str = dt_val.isoformat()
        else:
            dt_str = str(dt_val)

        intents = getattr(result, "order_intents", None)
        if intents:
            for intent in intents:
                if intent.action == "cancel_entry":
                    continue
                self._rows.append({
                    "ts_event":      ts_ms,
                    "datetime":      dt_str,
                    "instrument_id": intent.instrument_id or self._instrument_id,
                    "side":          intent.side,
                    "order_type":    intent.order_type,
                    "trigger_price": intent.trigger_price if intent.trigger_price is not None else intent.price,
                    "quantity":      intent.quantity or self._trade_size,
                    "reason":        intent.reason or result.reason,
                    "status":        _STATUS,
                })
            return

        if result.entry_side is not None:
            self._rows.append({
                "ts_event":      ts_ms,
                "datetime":      dt_str,
                "instrument_id": self._instrument_id,
                "side":          result.entry_side,
                "order_type":    result.entry_order_type or "stop_market",
                "trigger_price": result.entry_price,
                "quantity":      self._trade_size,
                "reason":        result.reason,
                "status":        _STATUS,
            })
            log.info(
                "DryRun ENTRY intent: %s %s @ %.6g  reason=%r",
                result.entry_side,
                self._instrument_id,
                result.entry_price or 0.0,
                result.reason,
            )

        if result.exit_side is not None:
            self._rows.append({
                "ts_event":      ts_ms,
                "datetime":      dt_str,
                "instrument_id": self._instrument_id,
                "side":          result.exit_side,
                "order_type":    "market",
                "trigger_price": None,
                "quantity":      self._trade_size,
                "reason":        result.reason,
                "status":        _STATUS,
            })
            log.info(
                "DryRun EXIT intent: %s %s  reason=%r",
                result.exit_side,
                self._instrument_id,
                result.reason,
            )

    def to_dataframe(self) -> pd.DataFrame:
        if not self._rows:
            return pd.DataFrame(columns=_COLUMNS)
        return pd.DataFrame(self._rows, columns=_COLUMNS)

    def to_csv(self, path: str | Path) -> Path:
        dest = Path(path)
        dest.parent.mkdir(parents=True, exist_ok=True)
        df = self.to_dataframe()
        df.to_csv(dest, index=False)
        log.info("DryRunExecutionRecorder: saved %d rows → %s", len(df), dest)
        return dest

    def to_parquet(self, path: str | Path) -> Path:
        dest = Path(path)
        dest.parent.mkdir(parents=True, exist_ok=True)
        df = self.to_dataframe()
        df.to_parquet(dest, index=False, engine="pyarrow")
        log.info("DryRunExecutionRecorder: saved %d rows → %s", len(df), dest)
        return dest

    def __len__(self) -> int:
        return len(self._rows)
