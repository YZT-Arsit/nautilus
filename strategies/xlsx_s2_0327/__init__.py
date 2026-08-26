"""Workbook strategy xlsx_s2_0327; source provenance is in config.yaml."""

from strategies.xlsx_s2_0327.config import XlsxS20327Config
from strategies.xlsx_s2_0327.plugin import PLUGIN
from strategies.xlsx_s2_0327.strategy import XlsxS20327Strategy

__all__ = ["PLUGIN", "XlsxS20327Config", "XlsxS20327Strategy"]
