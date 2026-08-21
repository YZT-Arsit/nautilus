"""Workbook strategy xlsx_s2_0809; source provenance is in config.yaml."""

from strategies.xlsx_s2_0809.config import XlsxS20809Config
from strategies.xlsx_s2_0809.plugin import PLUGIN
from strategies.xlsx_s2_0809.strategy import XlsxS20809Strategy

__all__ = ["PLUGIN", "XlsxS20809Config", "XlsxS20809Strategy"]
