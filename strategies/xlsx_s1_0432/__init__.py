"""Workbook strategy xlsx_s1_0432; source provenance is in config.yaml."""

from strategies.xlsx_s1_0432.config import XlsxS10432Config
from strategies.xlsx_s1_0432.plugin import PLUGIN
from strategies.xlsx_s1_0432.strategy import XlsxS10432Strategy

__all__ = ["PLUGIN", "XlsxS10432Config", "XlsxS10432Strategy"]
