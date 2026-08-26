"""Workbook strategy xlsx_s2_0769; source provenance is in config.yaml."""

from strategies.xlsx_s2_0769.config import XlsxS20769Config
from strategies.xlsx_s2_0769.plugin import PLUGIN
from strategies.xlsx_s2_0769.strategy import XlsxS20769Strategy

__all__ = ["PLUGIN", "XlsxS20769Config", "XlsxS20769Strategy"]
