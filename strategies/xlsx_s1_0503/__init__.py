"""Workbook strategy xlsx_s1_0503; source provenance is in config.yaml."""

from strategies.xlsx_s1_0503.config import XlsxS10503Config
from strategies.xlsx_s1_0503.plugin import PLUGIN
from strategies.xlsx_s1_0503.strategy import XlsxS10503Strategy

__all__ = ["PLUGIN", "XlsxS10503Config", "XlsxS10503Strategy"]
