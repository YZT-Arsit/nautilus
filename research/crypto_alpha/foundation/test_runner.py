"""Capture actual pytest statuses for the standard run."""
from pathlib import Path
import sys

import pytest


class Cases:
    def __init__(self):
        self.results = {}

    def pytest_runtest_logreport(self, report):
        if report.when == "call" or report.failed or report.skipped:
            self.results[report.nodeid] = report.outcome


def execute(root: Path):
    plugin = Cases()
    code = pytest.main([str(root/"tests"), "-q", "--disable-warnings", "-p", "no:cacheprovider"], plugins=[plugin])
    report = {outcome: sum(s == outcome for s in plugin.results.values())
              for outcome in ("passed","failed","skipped")}
    return dict(exit_code=int(code), cases=plugin.results, python=sys.version, **report)
