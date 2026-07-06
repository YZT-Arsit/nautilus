#!/usr/bin/env python3
"""READ-ONLY architecture inspector (code + features + boundaries).

Generates, under ``outputs/architecture_inventory/``:

* ``code_inventory.csv``        — one row per source/config/test/doc file with
  module_area, file_type, likely_role and actual import flags.
* ``feature_inventory.csv``     — the feature-library operators (builders) that
  the feature engine exposes, with reuse metadata.
* ``module_boundary_check.csv`` — layering-rule findings (import-line based), with
  explicit ``clean`` rows when a rule is satisfied.

This script **reads code only**. It imports nothing from the business layers, it
mutates no business module, and it writes exclusively under
``outputs/architecture_inventory/``. Import flags are detected from real
``import`` / ``from`` statements (comments and docstrings are ignored), so a file
that merely *mentions* ``nautilus_trader`` in prose is not flagged.
"""
from __future__ import annotations

import ast
import csv
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
_OUT = _REPO / "outputs" / "architecture_inventory"

# Directories scanned for the code inventory (existing ones only).
_SCAN_DIRS = (
    "data_engine", "feature_engine", "strategy_framework", "strategies",
    "scripts", "research", "configs", "tests_platform", "tests",
)
_TOP_FILES = ("run_strategy.py", "run_batch.py")


# --------------------------------------------------------------------------- #
# import detection (real statements only, via AST)
# --------------------------------------------------------------------------- #

def _import_roots(py_path: Path) -> set[str]:
    """Top-level package names actually imported by a .py file (AST-based)."""
    roots: set[str] = set()
    try:
        tree = ast.parse(py_path.read_text(encoding="utf-8"))
    except (SyntaxError, UnicodeDecodeError, OSError):
        return roots
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                roots.add(a.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0 and node.module:
                roots.add(node.module.split(".")[0])
    return roots


# --------------------------------------------------------------------------- #
# module_area classification (ordered path rules)
# --------------------------------------------------------------------------- #

def _module_area(rel: str) -> str:
    r = rel.replace("\\", "/")
    rules = [
        ("configs/", "config"),
        ("tests_platform/", "test"),
        ("tests/", "test"),
        # data_engine
        ("data_engine/schema.py", "data_schema"),
        ("data_engine/validation.py", "data_schema"),
        ("data_engine/events.py", "data_schema"),
        ("data_engine/time.py", "data_schema"),
        ("data_engine/split.py", "data_schema"),
        ("data_engine/adapters/", "data_schema"),
        ("data_engine/transforms/", "data_schema"),
        ("data_engine/sources/parquet", "data_storage"),
        ("data_engine/sources/hive_partitioning", "data_storage"),
        ("data_engine/historical/catalog", "data_storage"),
        ("data_engine/historical/manifest", "data_storage"),
        ("data_engine/", "data_ingestion"),
        # feature_engine
        ("feature_engine/storage/", "data_storage"),
        ("feature_engine/data_sources/", "data_ingestion"),
        ("feature_engine/services/", "data_ingestion"),
        ("feature_engine/compute/feature_lib/", "feature_operator"),
        ("feature_engine/compute/features.py", "feature_operator"),
        ("feature_engine/", "feature_orchestration"),
        # strategy framework
        ("strategy_framework/backends/", "backtest_runner"),
        ("strategy_framework/execution/backtest_report", "evaluation"),
        ("strategy_framework/execution/reports", "evaluation"),
        ("strategy_framework/execution/", "backtest_runner"),
        ("strategy_framework/output.py", "evaluation"),
        ("strategy_framework/backtest.py", "backtest_runner"),
        ("strategy_framework/", "strategy"),
        ("strategies/", "strategy"),
        # scripts (per-prefix)
        ("scripts/ingest", "data_ingestion"),
        ("scripts/build_minute_bars", "data_ingestion"),
        ("scripts/manage_historical_data", "data_ingestion"),
        ("scripts/migrate_market_data_layout", "data_storage"),
        ("scripts/inspect_data_storage", "data_storage"),
        ("scripts/inspect_feature_architecture", "evaluation"),
        ("scripts/validate_data_engine", "test"),
        ("scripts/verify_decoupling", "test"),
        ("scripts/run_bar_loader_smoke", "backtest_runner"),
        ("scripts/run_vwm_batch_backtests", "backtest_runner"),
        ("scripts/run_ma_crossover_demo", "backtest_runner"),
        ("scripts/dry_run_strategy_config", "backtest_runner"),
        ("scripts/binance_live_smoke", "data_ingestion"),
        ("run_strategy.py", "backtest_runner"),
        ("run_batch.py", "backtest_runner"),
    ]
    for prefix, area in rules:
        if r == prefix or r.startswith(prefix):
            return area
    return "unknown"


def _likely_role(rel: str, area: str) -> str:
    name = Path(rel).name
    if name == "__init__.py":
        return "package init / exports"
    if name.endswith(".md"):
        return "documentation"
    if name.endswith((".yaml", ".yml")):
        return "config"
    return area.replace("_", " ")


def _iter_files():
    seen = set()
    for d in _SCAN_DIRS:
        base = _REPO / d
        if not base.exists():
            continue
        for p in base.rglob("*"):
            if p.is_dir() or "__pycache__" in p.parts:
                continue
            if p.suffix not in (".py", ".yaml", ".yml", ".md"):
                continue
            seen.add(p)
    for f in _TOP_FILES:
        p = _REPO / f
        if p.exists():
            seen.add(p)
    return sorted(seen)


def build_code_inventory() -> list[dict]:
    rows = []
    for p in _iter_files():
        rel = str(p.relative_to(_REPO))
        area = _module_area(rel)
        roots = _import_roots(p) if p.suffix == ".py" else set()
        rows.append({
            "path": rel,
            "module_area": area,
            "file_type": p.suffix.lstrip("."),
            "likely_role": _likely_role(rel, area),
            "imports_data_engine": "data_engine" in roots,
            "imports_feature_engine": "feature_engine" in roots,
            "imports_nautilus": "nautilus_trader" in roots,
            "imports_strategy": ("strategies" in roots) or ("strategy_framework" in roots),
            "notes": "",
        })
    return rows


# --------------------------------------------------------------------------- #
# boundary check
# --------------------------------------------------------------------------- #

def build_boundary_rows(code_rows: list[dict]) -> list[dict]:
    out: list[dict] = []

    def add(fp, vtype, imp, sev, status, notes):
        out.append({"file_path": fp, "violation_type": vtype, "import_found": imp,
                    "severity": sev, "status": status, "notes": notes})

    # Rule 1/2: feature_engine must not import nautilus_trader or strategies.
    fe = [r for r in code_rows if r["path"].startswith("feature_engine/") and r["file_type"] == "py"]
    fe_naut = [r for r in fe if r["imports_nautilus"]]
    fe_strat = [r for r in fe if r["imports_strategy"]]
    for r in fe_naut:
        add(r["path"], "feature_engine_imports_nautilus", "nautilus_trader", "high", "violation", "")
    for r in fe_strat:
        add(r["path"], "feature_engine_imports_strategy", "strategies/strategy_framework", "high", "violation", "")
    if not fe_naut:
        add("feature_engine/**", "feature_engine_imports_nautilus", "", "info", "clean",
            f"{len(fe)} feature_engine .py files, 0 import nautilus_trader (incl. compute/feature_lib)")
    if not fe_strat:
        add("feature_engine/**", "feature_engine_imports_strategy", "", "info", "clean",
            f"{len(fe)} feature_engine .py files, 0 import strategy layers")

    # Rule 3: data_engine must not import strategies (and stays nautilus-free).
    de = [r for r in code_rows if r["path"].startswith("data_engine/") and r["file_type"] == "py"]
    de_strat = [r for r in de if r["imports_strategy"]]
    de_naut = [r for r in de if r["imports_nautilus"]]
    for r in de_strat:
        add(r["path"], "data_engine_imports_strategy", "strategies/strategy_framework", "high", "violation", "")
    for r in de_naut:
        add(r["path"], "data_engine_imports_nautilus", "nautilus_trader", "medium", "violation", "")
    if not de_strat:
        add("data_engine/**", "data_engine_imports_strategy", "", "info", "clean",
            f"{len(de)} data_engine .py files, 0 import strategy layers")
    if not de_naut:
        add("data_engine/**", "data_engine_imports_nautilus", "", "info", "clean",
            f"{len(de)} data_engine .py files, 0 import nautilus_trader")

    # Rule 6 (informational): strategies consume features only via feature_engine.api,
    # never deep compute/storage internals. Flag deep imports if any.
    deep = []
    for r in code_rows:
        if r["path"].startswith("strategies/") and r["file_type"] == "py":
            p = _REPO / r["path"]
            roots_ok = True
            try:
                tree = ast.parse(p.read_text(encoding="utf-8"))
            except Exception:
                continue
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.module and \
                        node.module.startswith("feature_engine.") and node.module != "feature_engine.api":
                    roots_ok = False
            if not roots_ok:
                deep.append(r["path"])
    for fp in deep:
        add(fp, "strategy_imports_feature_internals", "feature_engine.compute/...", "low", "violation",
            "strategy bypasses feature_engine.api facade")
    if not deep:
        add("strategies/**", "strategy_imports_feature_internals", "", "info", "clean",
            "all strategies import feature types via feature_engine.api facade only")

    # Informational: which strategies import nautilus_trader (allowed, but noted —
    # explains local test skips for those).
    strat_naut = [r["path"] for r in code_rows
                  if r["path"].startswith("strategies/") and r["imports_nautilus"]]
    add("strategies/**", "strategy_imports_nautilus_INFO", ",".join(strat_naut) or "",
        "info", "allowed",
        f"{len(strat_naut)} strategy files import nautilus_trader (allowed; strategy MAY use Nautilus indicators)")

    return out


# --------------------------------------------------------------------------- #
# feature operator inventory (curated from feature_engine/builders.py)
# --------------------------------------------------------------------------- #

_FL = "feature_engine/compute/feature_lib"
# (feature_name, builder, file, inputs, output, stateful, rolling_window)
_FEATURES = [
    ("rolling_mean", "rolling_mean_spec", "feature_engine/builders.py + compute/features.py",
     "one bar field (default close)", "rolling mean scalar", "stateful", "window (bars)"),
    ("rolling_range", "rolling_range_spec", f"{_FL}/price_action.py", "bar high,low", "high-low", "stateless", "1"),
    ("true_range", "true_range_spec", f"{_FL}/price_action.py", "bar high,low,prev_close", "true range", "stateful", "2"),
    ("candle_body_ratio", "candle_body_ratio_spec", f"{_FL}/price_action.py", "bar o,h,l,c", "|c-o|/(h-l)", "stateless", "1"),
    ("upper_shadow_ratio", "upper_shadow_ratio_spec", f"{_FL}/price_action.py", "bar o,h,l,c", "upper shadow ratio", "stateless", "1"),
    ("lower_shadow_ratio", "lower_shadow_ratio_spec", f"{_FL}/price_action.py", "bar o,h,l,c", "lower shadow ratio", "stateless", "1"),
    ("return_n", "return_n_spec", f"{_FL}/returns.py", "close", "close/close[-n]-1", "stateful", "window (n)"),
    ("momentum_n", "momentum_n_spec", f"{_FL}/returns.py", "close", "close-close[-n]", "stateful", "window (n)"),
    ("price_position", "price_position_spec", f"{_FL}/price_action.py", "bar h,l,c", "(c-min_low)/(max_high-min_low)", "stateful", "window"),
    ("drawdown_from_rolling_high", "drawdown_from_rolling_high_spec", f"{_FL}/returns.py", "close", "close/rolling_max-1", "stateful", "window"),
    ("breakout_up", "breakout_up_spec", f"{_FL}/price_action.py", "bar h,c", "close>prev rolling_max(high)", "stateful", "window"),
    ("breakout_down", "breakout_down_spec", f"{_FL}/price_action.py", "bar l,c", "close<prev rolling_min(low)", "stateful", "window"),
    ("atr", "atr_spec", f"{_FL}/volatility.py", "bar h,l,prev_close", "mean true range", "stateful", "window"),
    ("volatility_ratio", "volatility_ratio_spec", f"{_FL}/volatility.py", "close", "rv(short)/rv(long)", "stateful", "short+long"),
    ("bollinger_width", "bollinger_width_spec", f"{_FL}/volatility.py", "close", "(upper-lower)/middle", "stateful", "window"),
    ("bollinger_percent_b", "bollinger_percent_b_spec", f"{_FL}/volatility.py", "close", "(c-lower)/(upper-lower)", "stateful", "window"),
    ("zscore", "zscore_spec", f"{_FL}/normalization.py", "close", "(x-mean)/std", "stateful", "window"),
    ("volume_zscore", "volume_zscore_spec", f"{_FL}/normalization.py", "volume", "z-score of volume", "stateful", "window"),
    ("volume_ratio", "volume_ratio_spec", f"{_FL}/volume.py", "volume", "volume/mean(volume)", "stateful", "window"),
    ("quote_volume", "quote_volume_spec", f"{_FL}/volume.py", "bar quote_volume or close*volume", "quote volume", "stateless", "1"),
    ("vwap_distance", "vwap_distance_spec", f"{_FL}/volume.py", "close,volume", "close/vwap-1", "stateful", "window or session"),
    ("trade_count", "trade_count_spec", f"{_FL}/trade.py", "trade stream", "trades in window", "stateful", "time window"),
    ("trade_volume_sum", "trade_volume_sum_spec", f"{_FL}/trade.py", "trade quantity", "sum quantity", "stateful", "N trades"),
    ("trade_quote_volume_sum", "trade_quote_volume_sum_spec", f"{_FL}/trade.py", "trade quote_quantity", "sum quote qty", "stateful", "N trades"),
    ("avg_trade_size", "avg_trade_size_spec", f"{_FL}/trade.py", "trade quantity", "mean quantity", "stateful", "N trades"),
    ("signed_trade_volume", "signed_trade_volume_spec", f"{_FL}/trade.py", "trade qty+side", "signed qty sum", "stateful", "N trades"),
    ("trade_imbalance", "trade_imbalance_spec", f"{_FL}/trade.py", "trade qty+side", "(buy-sell)/(buy+sell)", "stateful", "N trades"),
    ("trade_vwap", "trade_vwap_spec", f"{_FL}/trade.py", "trade price+qty", "sum(p*q)/sum(q)", "stateful", "N trades"),
    ("large_trade_ratio", "large_trade_ratio_spec", f"{_FL}/trade.py", "trade qty", "frac qty>=threshold", "stateful", "N trades"),
    ("trade_intensity", "trade_intensity_spec", f"{_FL}/trade.py", "trade stream", "trades/second", "stateful", "time window"),
]


def build_feature_inventory() -> list[dict]:
    rows = []
    for name, builder, fpath, inputs, outputs, stateful, window in _FEATURES:
        rows.append({
            "feature_name": name,
            "file_path": fpath,
            "function_or_class": builder,
            "inputs": inputs,
            "outputs": outputs,
            "stateful_or_stateless": stateful,
            "rolling_window": window,
            "depends_on_nautilus": False,
            "reusable_across_strategies": True,
            "currently_used_by": "none (registered strategies use passthrough rolling_mean_spec; "
                                 "vwm_* compute indicators via nautilus_trader in strategies/vwm_*/indicators.py)",
            "output_storage": "in-memory FeatureSnapshot (live); feature_data parquet only if HistoricalFeatureBuilder is run",
            "notes": "pure-python operator; dispatched by params['type'] in PythonBackend",
        })
    return rows


def _write_csv(path: Path, rows: list[dict], cols: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def main() -> None:
    code_rows = build_code_inventory()
    _write_csv(_OUT / "code_inventory.csv", code_rows,
               ["path", "module_area", "file_type", "likely_role",
                "imports_data_engine", "imports_feature_engine", "imports_nautilus",
                "imports_strategy", "notes"])

    feat_rows = build_feature_inventory()
    _write_csv(_OUT / "feature_inventory.csv", feat_rows,
               ["feature_name", "file_path", "function_or_class", "inputs", "outputs",
                "stateful_or_stateless", "rolling_window", "depends_on_nautilus",
                "reusable_across_strategies", "currently_used_by", "output_storage", "notes"])

    bnd_rows = build_boundary_rows(code_rows)
    _write_csv(_OUT / "module_boundary_check.csv", bnd_rows,
               ["file_path", "violation_type", "import_found", "severity", "status", "notes"])

    violations = [r for r in bnd_rows if r["status"] == "violation"]
    print(f"code_inventory rows: {len(code_rows)}")
    print(f"feature_inventory rows: {len(feat_rows)}")
    print(f"module_boundary rows: {len(bnd_rows)} (violations: {len(violations)})")
    by_area: dict[str, int] = {}
    for r in code_rows:
        by_area[r["module_area"]] = by_area.get(r["module_area"], 0) + 1
    print("module_area counts:", dict(sorted(by_area.items())))


if __name__ == "__main__":
    main()
