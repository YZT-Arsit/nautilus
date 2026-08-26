"""Workbook strategy xlsx_s2_0870; source provenance is in config.yaml."""

from strategies.xlsx_s2_0870.config import XlsxS20870Config
from strategies.xlsx_s2_0870.plugin import PLUGIN
from strategies.xlsx_s2_0870.strategy import XlsxS20870Strategy

__all__ = ["PLUGIN", "XlsxS20870Config", "XlsxS20870Strategy"]
