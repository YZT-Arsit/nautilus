"""Workbook strategy xlsx_s2_0606; source provenance is in config.yaml."""

from strategies.xlsx_s2_0606.config import XlsxS20606Config
from strategies.xlsx_s2_0606.plugin import PLUGIN
from strategies.xlsx_s2_0606.strategy import XlsxS20606Strategy

__all__ = ["PLUGIN", "XlsxS20606Config", "XlsxS20606Strategy"]
