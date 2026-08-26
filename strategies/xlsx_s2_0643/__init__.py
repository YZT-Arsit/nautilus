"""Workbook strategy xlsx_s2_0643; source provenance is in config.yaml."""

from strategies.xlsx_s2_0643.config import XlsxS20643Config
from strategies.xlsx_s2_0643.plugin import PLUGIN
from strategies.xlsx_s2_0643.strategy import XlsxS20643Strategy

__all__ = ["PLUGIN", "XlsxS20643Config", "XlsxS20643Strategy"]
