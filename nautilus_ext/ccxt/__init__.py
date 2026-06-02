"""
nautilus_ext.ccxt — ccxt-based market data connector for NautilusTrader.

Public API
----------
    CcxtDataConfig          Configuration dataclass.
    CcxtBarDataConnector    High-level connector; compatible with NautilusBacktestRunner.

Lower-level helpers (import explicitly if needed)
-------------------------------------------------
    CcxtMarketConnector     Download / filter exchange markets.
    CcxtOhlcvConnector      Paginated OHLCV download.
    CcxtInstrumentMapper    ccxt market dict → Nautilus Instrument.
    CcxtBarMapper           OHLCV DataFrame → list[Bar].
    CcxtCache               File-system artefact persistence.
"""
__all__ = [
    "CcxtDataConfig",
    "CcxtBarDataConnector",
]


def __getattr__(name: str):
    if name == "CcxtDataConfig":
        from nautilus_ext.ccxt.ccxt_config import CcxtDataConfig
        return CcxtDataConfig
    if name == "CcxtBarDataConnector":
        from nautilus_ext.ccxt.ccxt_connector import CcxtBarDataConnector
        return CcxtBarDataConnector
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
