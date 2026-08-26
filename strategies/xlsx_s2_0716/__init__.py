"""Workbook strategy xlsx_s2_0716; source provenance is in config.yaml."""

from strategies.xlsx_s2_0716.config import XlsxS20716Config
from strategies.xlsx_s2_0716.plugin import PLUGIN
from strategies.xlsx_s2_0716.strategy import XlsxS20716Strategy

__all__ = ["PLUGIN", "XlsxS20716Config", "XlsxS20716Strategy"]
