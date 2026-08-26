"""Workbook strategy xlsx_s2_0256; source provenance is in config.yaml."""

from strategies.xlsx_s2_0256.config import XlsxS20256Config
from strategies.xlsx_s2_0256.plugin import PLUGIN
from strategies.xlsx_s2_0256.strategy import XlsxS20256Strategy

__all__ = ["PLUGIN", "XlsxS20256Config", "XlsxS20256Strategy"]
