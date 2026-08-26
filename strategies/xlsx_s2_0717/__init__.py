"""Workbook strategy xlsx_s2_0717; source provenance is in config.yaml."""

from strategies.xlsx_s2_0717.config import XlsxS20717Config
from strategies.xlsx_s2_0717.plugin import PLUGIN
from strategies.xlsx_s2_0717.strategy import XlsxS20717Strategy

__all__ = ["PLUGIN", "XlsxS20717Config", "XlsxS20717Strategy"]
