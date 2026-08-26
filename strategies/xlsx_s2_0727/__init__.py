"""Workbook strategy xlsx_s2_0727; source provenance is in config.yaml."""

from strategies.xlsx_s2_0727.config import XlsxS20727Config
from strategies.xlsx_s2_0727.plugin import PLUGIN
from strategies.xlsx_s2_0727.strategy import XlsxS20727Strategy

__all__ = ["PLUGIN", "XlsxS20727Config", "XlsxS20727Strategy"]
