"""Workbook strategy xlsx_s2_0603; source provenance is in config.yaml."""

from strategies.xlsx_s2_0603.config import XlsxS20603Config
from strategies.xlsx_s2_0603.plugin import PLUGIN
from strategies.xlsx_s2_0603.strategy import XlsxS20603Strategy

__all__ = ["PLUGIN", "XlsxS20603Config", "XlsxS20603Strategy"]
