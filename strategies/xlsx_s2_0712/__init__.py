"""Workbook strategy xlsx_s2_0712; source provenance is in config.yaml."""

from strategies.xlsx_s2_0712.config import XlsxS20712Config
from strategies.xlsx_s2_0712.plugin import PLUGIN
from strategies.xlsx_s2_0712.strategy import XlsxS20712Strategy

__all__ = ["PLUGIN", "XlsxS20712Config", "XlsxS20712Strategy"]
