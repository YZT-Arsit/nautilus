"""Workbook strategy xlsx_s1_0485; source provenance is in config.yaml."""

from strategies.xlsx_s1_0485.config import XlsxS10485Config
from strategies.xlsx_s1_0485.plugin import PLUGIN
from strategies.xlsx_s1_0485.strategy import XlsxS10485Strategy

__all__ = ["PLUGIN", "XlsxS10485Config", "XlsxS10485Strategy"]
