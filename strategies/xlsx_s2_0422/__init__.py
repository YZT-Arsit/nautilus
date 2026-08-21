"""Workbook strategy xlsx_s2_0422; source provenance is in config.yaml."""

from strategies.xlsx_s2_0422.config import XlsxS20422Config
from strategies.xlsx_s2_0422.plugin import PLUGIN
from strategies.xlsx_s2_0422.strategy import XlsxS20422Strategy

__all__ = ["PLUGIN", "XlsxS20422Config", "XlsxS20422Strategy"]
