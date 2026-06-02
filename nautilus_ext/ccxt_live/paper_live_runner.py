"""
CcxtPaperLiveRunner — lightweight polling paper live runner.

This is NOT a Nautilus TradingNode / LiveDataEngine.  It is a standalone
Python loop that:
  1. Downloads warmup bars via CcxtPollingBarFeed.
  2. Pre-heats a pure-Python signal engine with those bars.
  3. Polls ccxt REST for new complete bars at regular intervals.
  4. For each new bar, calls signal_engine.update(BarInput) and records the output.
  5. Logs entry/exit intents to DryRunExecutionRecorder (no real orders).
  6. Saves CSV/Parquet/JSON artefacts to output_dir.

Why not a full TradingNode?
  BaseBarStrategy depends on Nautilus Portfolio/cache/order_factory which are
  only available inside a BacktestEngine or TradingNode.  VwmFeatureEngine and
  VolumeWeightedMomentumShortSignalEngine are pure Python and can be driven
  directly, making TradingNode unnecessary for paper live validation.

See docs/nautilus_ext_ccxt_polling_live.md for the upgrade path to TradingNode.
"""
from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from nautilus_ext.ccxt_live.dry_run_execution import DryRunExecutionRecorder
from nautilus_ext.ccxt_live.polling_bar_feed import CcxtPollingBarFeed
from nautilus_ext.ccxt_live.polling_config import CcxtPollingLiveConfig
from nautilus_ext.ccxt_live.signal_recorder import SignalRecorder

log = logging.getLogger(__name__)


class CcxtPaperLiveRunner:
    """Drive a pure-Python signal engine with ccxt REST bar polling.

    Parameters
    ----------
    config : CcxtPollingLiveConfig
        Full paper live session configuration.
    signal_engine : any
        Must implement:
            signal_engine.update(bar: BarInput, position: int, bars_since_entry: int) -> SignalResult
        Typically VolumeWeightedMomentumShortSignalEngine, but any engine with
        this interface is accepted.
    _feed : CcxtPollingBarFeed | None
        Inject a pre-built feed (used for testing without real network calls).
    """

    def __init__(
        self,
        config: CcxtPollingLiveConfig,
        signal_engine,
        _feed: CcxtPollingBarFeed | None = None,
    ) -> None:
        self.config = config
        self.signal_engine = signal_engine

        self._feed = _feed or CcxtPollingBarFeed(config)
        self._position: int = 0
        self._bars_since_entry: int = 0

        self._signal_recorder: SignalRecorder | None = None
        self._exec_recorder: DryRunExecutionRecorder | None = None
        self._received_bars: list[dict] = []

        self._start_time: float | None = None
        self._total_bars: int = 0

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def run(self, max_bars: int | None = None) -> dict:
        """Start the paper live loop.

        Parameters
        ----------
        max_bars : int | None
            Stop after this many new bars.  Overrides config.max_bars.
            If both are None, the loop runs until interrupted (Ctrl-C) or
            config.max_runtime_seconds is reached.

        Returns
        -------
        dict
            Summary: total_bars, total_signals, total_orders, elapsed_seconds.
        """
        effective_max_bars = max_bars if max_bars is not None else self.config.max_bars
        max_runtime = self.config.max_runtime_seconds

        # --- initialization -------------------------------------------
        if not self._feed._initialized:
            self._feed.initialize()

        instrument_id = str(self._feed.instrument.id)
        bar_type_str = self._feed.bar_type_str

        self._signal_recorder = SignalRecorder(instrument_id, bar_type_str)
        self._exec_recorder = DryRunExecutionRecorder(instrument_id, self.config.trade_size)

        # --- warmup ---------------------------------------------------
        log.info("=== Paper Live Warmup  symbol=%r ===", self.config.symbol)
        warmup_df = self._feed.warmup()
        self._warmup_signal_engine(warmup_df)

        # --- main loop ------------------------------------------------
        log.info(
            "=== Paper Live Loop  symbol=%r  poll=%.0fs  max_bars=%s ===",
            self.config.symbol, self.config.poll_interval_seconds, effective_max_bars,
        )
        self._start_time = time.time()
        self._total_bars = 0

        try:
            while True:
                # --- runtime limit ------------------------------------
                elapsed = time.time() - self._start_time
                if max_runtime is not None and elapsed >= max_runtime:
                    log.info("max_runtime_seconds=%.0f reached; stopping.", max_runtime)
                    break

                # --- poll new bars ------------------------------------
                new_df = self._feed.poll_once()

                # --- process each new bar -----------------------------
                for _, row in new_df.iterrows():
                    self._process_bar(row)
                    self._total_bars += 1

                    if effective_max_bars is not None and self._total_bars >= effective_max_bars:
                        log.info("max_bars=%d reached; stopping.", effective_max_bars)
                        break

                # Inner break must propagate to outer loop.
                if effective_max_bars is not None and self._total_bars >= effective_max_bars:
                    break

                time.sleep(self.config.poll_interval_seconds)

        except KeyboardInterrupt:
            log.info("Interrupted by user; saving outputs …")
        finally:
            total_elapsed = time.time() - (self._start_time or time.time())
            self._save_outputs(total_elapsed)

        return {
            "total_bars":    self._total_bars,
            "total_signals": len(self._signal_recorder) if self._signal_recorder else 0,
            "total_orders":  len(self._exec_recorder) if self._exec_recorder else 0,
            "elapsed_seconds": time.time() - (self._start_time or time.time()),
        }

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _warmup_signal_engine(self, warmup_df: pd.DataFrame) -> None:
        from nautilus_ext.strategies.signal_types import BarInput
        if warmup_df.empty:
            log.warning("Warmup DataFrame is empty; signal engine will cold-start.")
            return
        for _, row in warmup_df.iterrows():
            bar_input = BarInput(
                open=float(row["open"]),
                high=float(row["high"]),
                low=float(row["low"]),
                close=float(row["close"]),
                volume=float(row["volume"]),
            )
            self.signal_engine.update(
                bar_input,
                position=self._position,
                bars_since_entry=self._bars_since_entry,
            )
        log.info("Signal engine warmed up with %d bars.", len(warmup_df))

    def _process_bar(self, row: "pd.Series") -> None:
        from nautilus_ext.strategies.signal_types import BarInput

        bar_input = BarInput(
            open=float(row["open"]),
            high=float(row["high"]),
            low=float(row["low"]),
            close=float(row["close"]),
            volume=float(row["volume"]),
        )

        result = self.signal_engine.update(
            bar_input,
            position=self._position,
            bars_since_entry=self._bars_since_entry,
        )

        self._update_position(result)
        self._signal_recorder.append(row, result, self._position)

        if result.entry_side is not None or result.exit_side is not None:
            self._exec_recorder.append(row, result)

        # Track for received_bars output
        self._received_bars.append({
            "ts_event":   int(row["timestamp_ms"]),
            "datetime":   str(row.get("datetime", "")),
            "open":       float(row["open"]),
            "high":       float(row["high"]),
            "low":        float(row["low"]),
            "close":      float(row["close"]),
            "volume":     float(row["volume"]),
        })

    def _update_position(self, result) -> None:
        if result.entry_side == "SELL":
            self._position = -1
            self._bars_since_entry = 0
        elif result.exit_side == "BUY":
            self._position = 0
            self._bars_since_entry = 0
        elif self._position == -1:
            self._bars_since_entry += 1

    def _save_outputs(self, elapsed: float) -> None:
        if not self.config.output_dir:
            return

        out = Path(self.config.output_dir)
        out.mkdir(parents=True, exist_ok=True)

        # received_bars
        if self._received_bars:
            bars_df = pd.DataFrame(self._received_bars)
            bars_df.to_csv(out / "received_bars.csv", index=False)
            bars_df.to_parquet(out / "received_bars.parquet", index=False, engine="pyarrow")
            log.info("Saved received_bars (%d rows) → %s", len(bars_df), out)

        # signals
        if self._signal_recorder is not None:
            self._signal_recorder.to_csv(out / "signals.csv")
            if len(self._signal_recorder) > 0:
                self._signal_recorder.to_parquet(out / "signals.parquet")

        # orders (always write; empty file when no intents were recorded)
        if self._exec_recorder is not None:
            self._exec_recorder.to_csv(out / "orders.csv")

        # run_info.json — no secrets included
        try:
            instrument_id = str(self._feed.instrument.id) if self._feed._initialized else "unknown"
            bar_type = self._feed.bar_type_str if self._feed._initialized else "unknown"
        except Exception:
            instrument_id = "unknown"
            bar_type = "unknown"

        run_info = {
            "exchange_id":          self.config.exchange_id,
            "symbol":               self.config.symbol,
            "market_type":          self.config.market_type,
            "timeframe":            self.config.timeframe,
            "venue":                self.config.resolved_venue,
            "instrument_id":        instrument_id,
            "bar_type":             bar_type,
            "warmup_bars":          self.config.warmup_bars,
            "poll_interval_seconds": self.config.poll_interval_seconds,
            "dry_run":              True,
            "enable_order_submit":  False,
            "total_bars_received":  self._total_bars,
            "total_signals":        len(self._signal_recorder) if self._signal_recorder else 0,
            "total_order_intents":  len(self._exec_recorder) if self._exec_recorder else 0,
            "elapsed_seconds":      round(elapsed, 2),
            "utc_end":              datetime.now(timezone.utc).isoformat(),
        }
        with (out / "run_info.json").open("w", encoding="utf-8") as fh:
            json.dump(run_info, fh, indent=2)
        log.info("Saved run_info.json → %s", out / "run_info.json")
