"""Workbook strategy xlsx_s2_0804; source provenance is in config.yaml."""

from strategies.xlsx_s2_0804.config import XlsxS20804Config
from strategies.xlsx_s2_0804.plugin import PLUGIN
from strategies.xlsx_s2_0804.strategy import XlsxS20804Strategy

__all__ = ["PLUGIN", "XlsxS20804Config", "XlsxS20804Strategy"]
