"""Workbook strategy xlsx_s2_0441; source provenance is in config.yaml."""

from strategies.xlsx_s2_0441.config import XlsxS20441Config
from strategies.xlsx_s2_0441.plugin import PLUGIN
from strategies.xlsx_s2_0441.strategy import XlsxS20441Strategy

__all__ = ["PLUGIN", "XlsxS20441Config", "XlsxS20441Strategy"]
