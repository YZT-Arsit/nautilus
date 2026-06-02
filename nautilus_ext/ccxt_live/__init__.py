"""
nautilus_ext.ccxt_live — ccxt REST polling / paper live runner.

Public API
----------
    CcxtPollingLiveConfig    Configuration dataclass for a paper live session.
    CcxtPaperLiveRunner      Lightweight polling runner (no TradingNode).

Lower-level helpers (import explicitly if needed)
-------------------------------------------------
    CcxtPollingBarFeed       Warmup + poll feed for a single symbol.
    SignalRecorder           Per-bar signal CSV/Parquet recorder.
    DryRunExecutionRecorder  Order intent recorder (no real submissions).
"""
__all__ = [
    "CcxtPollingLiveConfig",
    "CcxtPaperLiveRunner",
]


def __getattr__(name: str):
    if name == "CcxtPollingLiveConfig":
        from nautilus_ext.ccxt_live.polling_config import CcxtPollingLiveConfig
        return CcxtPollingLiveConfig
    if name == "CcxtPaperLiveRunner":
        from nautilus_ext.ccxt_live.paper_live_runner import CcxtPaperLiveRunner
        return CcxtPaperLiveRunner
    if name == "CcxtPollingBarFeed":
        from nautilus_ext.ccxt_live.polling_bar_feed import CcxtPollingBarFeed
        return CcxtPollingBarFeed
    if name == "SignalRecorder":
        from nautilus_ext.ccxt_live.signal_recorder import SignalRecorder
        return SignalRecorder
    if name == "DryRunExecutionRecorder":
        from nautilus_ext.ccxt_live.dry_run_execution import DryRunExecutionRecorder
        return DryRunExecutionRecorder
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
