"""Workbook strategy xlsx_s2_0845; source provenance is in config.yaml."""

from strategies.xlsx_s2_0845.config import XlsxS20845Config
from strategies.xlsx_s2_0845.plugin import PLUGIN
from strategies.xlsx_s2_0845.strategy import XlsxS20845Strategy

__all__ = ["PLUGIN", "XlsxS20845Config", "XlsxS20845Strategy"]
