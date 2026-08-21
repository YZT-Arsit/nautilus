"""Workbook strategy xlsx_s2_0726; source provenance is in config.yaml."""

from strategies.xlsx_s2_0726.config import XlsxS20726Config
from strategies.xlsx_s2_0726.plugin import PLUGIN
from strategies.xlsx_s2_0726.strategy import XlsxS20726Strategy

__all__ = ["PLUGIN", "XlsxS20726Config", "XlsxS20726Strategy"]
