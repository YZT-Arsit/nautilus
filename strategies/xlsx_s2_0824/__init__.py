"""Workbook strategy xlsx_s2_0824; source provenance is in config.yaml."""

from strategies.xlsx_s2_0824.config import XlsxS20824Config
from strategies.xlsx_s2_0824.plugin import PLUGIN
from strategies.xlsx_s2_0824.strategy import XlsxS20824Strategy

__all__ = ["PLUGIN", "XlsxS20824Config", "XlsxS20824Strategy"]
