"""Workbook strategy xlsx_s1_0033; source provenance is in config.yaml."""

from strategies.xlsx_s1_0033.config import XlsxS10033Config
from strategies.xlsx_s1_0033.plugin import PLUGIN
from strategies.xlsx_s1_0033.strategy import XlsxS10033Strategy

__all__ = ["PLUGIN", "XlsxS10033Config", "XlsxS10033Strategy"]
