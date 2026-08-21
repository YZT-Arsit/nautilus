"""Workbook strategy xlsx_s1_0516; source provenance is in config.yaml."""

from strategies.xlsx_s1_0516.config import XlsxS10516Config
from strategies.xlsx_s1_0516.plugin import PLUGIN
from strategies.xlsx_s1_0516.strategy import XlsxS10516Strategy

__all__ = ["PLUGIN", "XlsxS10516Config", "XlsxS10516Strategy"]
