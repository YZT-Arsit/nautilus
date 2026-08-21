"""Workbook strategy xlsx_s1_0508; source provenance is in config.yaml."""

from strategies.xlsx_s1_0508.config import XlsxS10508Config
from strategies.xlsx_s1_0508.plugin import PLUGIN
from strategies.xlsx_s1_0508.strategy import XlsxS10508Strategy

__all__ = ["PLUGIN", "XlsxS10508Config", "XlsxS10508Strategy"]
