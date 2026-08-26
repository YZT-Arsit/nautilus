"""Workbook strategy xlsx_s1_0502; source provenance is in config.yaml."""

from strategies.xlsx_s1_0502.config import XlsxS10502Config
from strategies.xlsx_s1_0502.plugin import PLUGIN
from strategies.xlsx_s1_0502.strategy import XlsxS10502Strategy

__all__ = ["PLUGIN", "XlsxS10502Config", "XlsxS10502Strategy"]
