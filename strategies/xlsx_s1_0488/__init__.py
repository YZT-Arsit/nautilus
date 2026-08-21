"""Workbook strategy xlsx_s1_0488; source provenance is in config.yaml."""

from strategies.xlsx_s1_0488.config import XlsxS10488Config
from strategies.xlsx_s1_0488.plugin import PLUGIN
from strategies.xlsx_s1_0488.strategy import XlsxS10488Strategy

__all__ = ["PLUGIN", "XlsxS10488Config", "XlsxS10488Strategy"]
