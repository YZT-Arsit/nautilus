"""Workbook strategy xlsx_s2_0433; source provenance is in config.yaml."""

from strategies.xlsx_s2_0433.config import XlsxS20433Config
from strategies.xlsx_s2_0433.plugin import PLUGIN
from strategies.xlsx_s2_0433.strategy import XlsxS20433Strategy

__all__ = ["PLUGIN", "XlsxS20433Config", "XlsxS20433Strategy"]
