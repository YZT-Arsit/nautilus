"""Workbook strategy xlsx_s2_0718; source provenance is in config.yaml."""

from strategies.xlsx_s2_0718.config import XlsxS20718Config
from strategies.xlsx_s2_0718.plugin import PLUGIN
from strategies.xlsx_s2_0718.strategy import XlsxS20718Strategy

__all__ = ["PLUGIN", "XlsxS20718Config", "XlsxS20718Strategy"]
