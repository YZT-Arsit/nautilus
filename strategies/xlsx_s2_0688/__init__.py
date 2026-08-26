"""Workbook strategy xlsx_s2_0688; source provenance is in config.yaml."""

from strategies.xlsx_s2_0688.config import XlsxS20688Config
from strategies.xlsx_s2_0688.plugin import PLUGIN
from strategies.xlsx_s2_0688.strategy import XlsxS20688Strategy

__all__ = ["PLUGIN", "XlsxS20688Config", "XlsxS20688Strategy"]
