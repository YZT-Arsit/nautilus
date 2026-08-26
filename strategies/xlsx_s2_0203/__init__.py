"""Workbook strategy xlsx_s2_0203; source provenance is in config.yaml."""

from strategies.xlsx_s2_0203.config import XlsxS20203Config
from strategies.xlsx_s2_0203.plugin import PLUGIN
from strategies.xlsx_s2_0203.strategy import XlsxS20203Strategy

__all__ = ["PLUGIN", "XlsxS20203Config", "XlsxS20203Strategy"]
