"""Workbook strategy xlsx_s2_0432; source provenance is in config.yaml."""

from strategies.xlsx_s2_0432.config import XlsxS20432Config
from strategies.xlsx_s2_0432.plugin import PLUGIN
from strategies.xlsx_s2_0432.strategy import XlsxS20432Strategy

__all__ = ["PLUGIN", "XlsxS20432Config", "XlsxS20432Strategy"]
