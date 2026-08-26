"""Workbook strategy xlsx_s2_0835; source provenance is in config.yaml."""

from strategies.xlsx_s2_0835.config import XlsxS20835Config
from strategies.xlsx_s2_0835.plugin import PLUGIN
from strategies.xlsx_s2_0835.strategy import XlsxS20835Strategy

__all__ = ["PLUGIN", "XlsxS20835Config", "XlsxS20835Strategy"]
