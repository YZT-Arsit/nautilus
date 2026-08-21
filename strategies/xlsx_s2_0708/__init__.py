"""Workbook strategy xlsx_s2_0708; source provenance is in config.yaml."""

from strategies.xlsx_s2_0708.config import XlsxS20708Config
from strategies.xlsx_s2_0708.plugin import PLUGIN
from strategies.xlsx_s2_0708.strategy import XlsxS20708Strategy

__all__ = ["PLUGIN", "XlsxS20708Config", "XlsxS20708Strategy"]
