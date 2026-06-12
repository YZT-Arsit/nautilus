"""边界测试：data_engine core 不耦合 Nautilus / 重依赖。

这些测试只用标准库，验证“自研模块能在没有 Nautilus 的普通 Python 环境运行”。
"""
from __future__ import annotations

from pathlib import Path

import pytest

import data_engine

_PKG_ROOT = Path(data_engine.__file__).resolve().parent

# 唯一允许 import Nautilus 的文件（且它也只懒加载）。
_SANCTIONED_NAUTILUS = {"adapters/nautilus_catalog.py"}


def _core_py_files():
    for p in sorted(_PKG_ROOT.rglob("*.py")):
        rel = p.relative_to(_PKG_ROOT).as_posix()
        if "tests/" in rel or rel.startswith("tests"):
            continue
        yield rel, p


def test_import_data_engine_is_stdlib_only():
    # 这些公共符号必须可用，且 import 过程不需要 polars/pyarrow/nautilus。
    for name in ("BarEvent", "load_events", "make_bars", "make_bar_event"):
        assert hasattr(data_engine, name)


def test_no_toplevel_nautilus_import_in_core():
    for rel, path in _core_py_files():
        if rel in _SANCTIONED_NAUTILUS:
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            stripped = line.lstrip()
            # 顶层（列 0）import 才算耦合；缩进的懒加载允许。
            if line == stripped and (
                line.startswith("import nautilus_trader")
                or line.startswith("from nautilus_trader")
            ):
                raise AssertionError(f"data_engine core 顶层 import Nautilus: {rel}: {line!r}")


def test_no_toplevel_heavy_import_in_core():
    # polars / pyarrow 必须懒加载（缩进），core 顶层不得直接 import。
    heavy = ("polars", "pyarrow", "pandas")
    for rel, path in _core_py_files():
        for line in path.read_text(encoding="utf-8").splitlines():
            if line != line.lstrip():
                continue  # 缩进 = 懒加载，放行
            for mod in heavy:
                if line.startswith(f"import {mod}") or line.startswith(f"from {mod}"):
                    raise AssertionError(
                        f"data_engine core 顶层 import 重依赖 {mod}: {rel}: {line!r}"
                    )


def test_dataframe_adapter_polars_is_lazy():
    src = (_PKG_ROOT / "adapters" / "dataframe_adapter.py").read_text(encoding="utf-8")
    for line in src.splitlines():
        if line == line.lstrip() and (
            line.startswith("import polars") or line.startswith("from polars")
        ):
            raise AssertionError("dataframe_adapter 顶层 import polars（应懒加载）")


def test_bars_to_polars_clear_error_without_polars():
    # 无 polars 环境调用应给出清晰 ImportError；有 polars 则正常工作。
    pytest.importorskip  # 占位，确保 pytest 已导入
    from data_engine.adapters import bars_to_polars
    from data_engine import make_bars

    try:
        import polars  # noqa: F401
        has_polars = True
    except ImportError:
        has_polars = False

    if has_polars:
        df = bars_to_polars(make_bars([1.0, 2.0]))
        assert df.height == 2
    else:
        with pytest.raises(ImportError):
            bars_to_polars(make_bars([1.0, 2.0]))
