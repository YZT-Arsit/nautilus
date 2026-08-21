"""Workbook strategy xlsx_s2_0842; source provenance is in config.yaml."""

from strategies.xlsx_s2_0842.config import XlsxS20842Config
from strategies.xlsx_s2_0842.plugin import PLUGIN
from strategies.xlsx_s2_0842.strategy import XlsxS20842Strategy

__all__ = ["PLUGIN", "XlsxS20842Config", "XlsxS20842Strategy"]
