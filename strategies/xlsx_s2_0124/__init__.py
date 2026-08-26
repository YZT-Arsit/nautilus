"""Workbook strategy xlsx_s2_0124; source provenance is in config.yaml."""

from strategies.xlsx_s2_0124.config import XlsxS20124Config
from strategies.xlsx_s2_0124.plugin import PLUGIN
from strategies.xlsx_s2_0124.strategy import XlsxS20124Strategy

__all__ = ["PLUGIN", "XlsxS20124Config", "XlsxS20124Strategy"]
