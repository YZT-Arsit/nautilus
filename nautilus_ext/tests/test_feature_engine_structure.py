from __future__ import annotations

import ast
from pathlib import Path


FEATURES_DIR = Path("feature_engine/features")
ROOT_FEATURE_ALLOWLIST = {
    "nautilus_indicators.py",
    "tradeblazer_features.py",
    "vwm_adapter.py",
    "vwm_features.py",
}


def test_feature_operator_directory_and_modules_exist():
    assert FEATURES_DIR.is_dir()
    for name in ("vwm.py", "moving_average.py", "rsi.py", "macd.py", "rolling_volatility.py", "derived.py"):
        assert (FEATURES_DIR / name).is_file()


def test_feature_modules_do_not_import_nautilus_package():
    for path in FEATURES_DIR.glob("*.py"):
        text = path.read_text(encoding="utf-8")
        assert "nautilus_" + "trader" not in text


def test_suspected_root_files_are_documented():
    doc = Path("docs/feature_engine_structure_audit.md").read_text(encoding="utf-8")
    for name in ("nautilus_indicators.py", "tradeblazer_features.py", "vwm_adapter.py", "vwm_features.py"):
        assert name in doc
    assert "keep_bridge" in doc
    assert "deprecated_compat" in doc
    assert "remove_later" in doc


def test_no_new_root_level_feature_operator_files_are_added():
    root_files = {path.name for path in Path("feature_engine").glob("*.py")}
    suspicious = {
        name
        for name in root_files
        if name.endswith("_features.py") or name.endswith("_indicators.py") or name.endswith("_adapter.py")
    }
    assert suspicious <= ROOT_FEATURE_ALLOWLIST


def test_root_legacy_files_remain_import_compatible():
    import feature_engine.tradeblazer_features as tradeblazer_features

    assert hasattr(tradeblazer_features, "RawMomentumFeature")
    assert hasattr(tradeblazer_features, "cross_over")
    assert hasattr(tradeblazer_features, "cross_under")


def test_feature_registry_loads_registered_feature_modules_from_features_dir():
    from feature_engine.core.registry import registry
    from feature_engine.features import load_all

    load_all()
    names = set(registry())
    assert {"vwm_20", "sma_20", "rsi_14", "macd", "vol_30", "vwm_zscore_60"} <= names


def test_root_files_are_not_registered_feature_operators():
    for path in (Path("feature_engine/tradeblazer_features.py"), Path("feature_engine/vwm_adapter.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        class_names = [node.name for node in ast.walk(tree) if isinstance(node, ast.ClassDef)]
        assert "VWM" not in class_names
        assert "SMA" not in class_names
        assert "RSI" not in class_names
        assert "MACD" not in class_names


def test_feature_structure_doc_states_policy():
    doc = Path("docs/feature_engine_structure_audit.md").read_text(encoding="utf-8")
    assert "New small feature operators must be added under `feature_engine/features/`" in doc
    assert "Do not introduce Nautilus trading package imports into `feature_engine/features/`" in doc
