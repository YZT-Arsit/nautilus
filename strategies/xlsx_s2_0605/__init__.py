"""Workbook strategy xlsx_s2_0605; source provenance is in config.yaml."""

from strategies.xlsx_s2_0605.config import XlsxS20605Config
from strategies.xlsx_s2_0605.plugin import PLUGIN
from strategies.xlsx_s2_0605.strategy import XlsxS20605Strategy

__all__ = ["PLUGIN", "XlsxS20605Config", "XlsxS20605Strategy"]
