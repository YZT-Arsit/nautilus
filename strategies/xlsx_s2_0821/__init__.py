"""Workbook strategy xlsx_s2_0821; source provenance is in config.yaml."""

from strategies.xlsx_s2_0821.config import XlsxS20821Config
from strategies.xlsx_s2_0821.plugin import PLUGIN
from strategies.xlsx_s2_0821.strategy import XlsxS20821Strategy

__all__ = ["PLUGIN", "XlsxS20821Config", "XlsxS20821Strategy"]
