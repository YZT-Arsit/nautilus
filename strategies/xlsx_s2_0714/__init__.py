"""Workbook strategy xlsx_s2_0714; source provenance is in config.yaml."""

from strategies.xlsx_s2_0714.config import XlsxS20714Config
from strategies.xlsx_s2_0714.plugin import PLUGIN
from strategies.xlsx_s2_0714.strategy import XlsxS20714Strategy

__all__ = ["PLUGIN", "XlsxS20714Config", "XlsxS20714Strategy"]
