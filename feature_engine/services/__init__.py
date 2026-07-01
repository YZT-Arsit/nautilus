"""feature_engine.services — reusable read/compute/write orchestration.

CLI scripts in ``scripts/`` only do argparse + call these services.

* :class:`MinuteBarBuilder` — tick/quote/bar -> standard minute bars -> ``market_data``.

Offline feature computation now lives in :mod:`feature_engine.offline`
(``HistoricalFeatureBuilder``), built on the spec engine.
"""
from feature_engine.services.minute_bar_builder import MinuteBarBuilder

__all__ = ["MinuteBarBuilder"]
