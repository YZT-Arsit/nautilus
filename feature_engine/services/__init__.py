"""feature_engine.services — 把读取/计算/落盘编排成可复用的服务。

CLI 脚本（``scripts/``）只负责 argparse + 调用这里的 service，业务逻辑都在
service 里，方便测试与跨项目复用。

* :class:`MinuteBarBuilder` —— tick/quote/bar -> 标准分钟线 -> market_data 落盘。
* :class:`HistoricalFeatureBuilder` —— market_data / DataFrame / events -> 特征
  -> feature_data 落盘。
"""
from feature_engine.services.historical_feature_builder import HistoricalFeatureBuilder
from feature_engine.services.minute_bar_builder import MinuteBarBuilder

__all__ = ["MinuteBarBuilder", "HistoricalFeatureBuilder"]
