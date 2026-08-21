"""Workbook strategy xlsx_s2_0721; source provenance is in config.yaml."""

from strategies.xlsx_s2_0721.config import XlsxS20721Config
from strategies.xlsx_s2_0721.plugin import PLUGIN
from strategies.xlsx_s2_0721.strategy import XlsxS20721Strategy

__all__ = ["PLUGIN", "XlsxS20721Config", "XlsxS20721Strategy"]
