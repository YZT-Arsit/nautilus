"""Workbook strategy xlsx_s2_0745; source provenance is in config.yaml."""

from strategies.xlsx_s2_0745.config import XlsxS20745Config
from strategies.xlsx_s2_0745.plugin import PLUGIN
from strategies.xlsx_s2_0745.strategy import XlsxS20745Strategy

__all__ = ["PLUGIN", "XlsxS20745Config", "XlsxS20745Strategy"]
