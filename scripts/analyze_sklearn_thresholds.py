#!/usr/bin/env python3
"""CLI: threshold / cost-aware analysis (B1.5) of trained sklearn baselines.

Reads each model's ``model.joblib`` + ``feature_columns.json`` from the B1
artifact dir and the dataset's ``split=validation`` parquet, then writes
``threshold_analysis.json`` next to the models and prints a compact per-model
table. Reads only the validation split (never test); trains nothing; runs no
backtest. Output is restricted to ``outputs/models/``.

Run on the server via ``uv run --no-sync python`` (sklearn/joblib live in
``.venv`` but not in ``uv.lock``)::

    uv run --no-sync python scripts/analyze_sklearn_thresholds.py \
        --dataset outputs/research_datasets/ml_v1_btcusdt_1m_train_val \
        --models-dir outputs/models/ml_v1_btcusdt_1m_sklearn_baseline
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

# Repo-root bootstrap (see scripts/build_ml_dataset.py for the why).
_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from research.threshold_analysis import (  # noqa: E402
    DEFAULT_THRESHOLDS,
    analyze_model,
    load_validation,
    write_analysis,
)

DEFAULT_MODELS = ("logistic_regression", "hist_gradient_boosting")
_ALLOWED_OUT_PREFIX = "outputs/models"


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Threshold/cost-aware analysis of sklearn baselines (B1.5)")
    p.add_argument("--dataset", required=True)
    p.add_argument("--models-dir", required=True)
    p.add_argument("--models", default=",".join(DEFAULT_MODELS))
    p.add_argument("--out", default=None, help="default: <models-dir>/threshold_analysis.json")
    p.add_argument("--max-validation-rows", type=int, default=None)
    p.add_argument("--dry-run", action="store_true")
    return p


def parse_models(raw: str) -> list[str]:
    return [m.strip() for m in str(raw).split(",") if m.strip()]


def _resolve_out(args) -> str:
    return args.out or str(Path(args.models_dir) / "threshold_analysis.json")


def preflight(args) -> dict[str, Any]:
    models = parse_models(args.models)
    if not models:
        raise ValueError("models must be non-empty")

    out_posix = Path(_resolve_out(args)).as_posix()
    if "historical_data" in out_posix:
        raise ValueError("refusing to write under historical_data")
    if "outputs/backtests" in out_posix:
        raise ValueError("refusing to write under outputs/backtests")
    if "outputs/research_datasets" in out_posix:
        raise ValueError("refusing to write under outputs/research_datasets")
    if _ALLOWED_OUT_PREFIX not in out_posix:
        raise ValueError(f"output must be under {_ALLOWED_OUT_PREFIX}/")

    ds = Path(args.dataset)
    if not (ds / "split=validation").exists():
        raise ValueError(f"dataset missing split=validation/ under {ds}")
    md = Path(args.models_dir)
    for name in models:
        if not (md / name / "model.joblib").exists():
            raise ValueError(f"missing model artifact: {md / name / 'model.joblib'}")
        if not (md / name / "feature_columns.json").exists():
            raise ValueError(f"missing feature_columns.json for model: {name}")
    return {"models": models, "dataset": str(ds), "models_dir": str(md), "out": _resolve_out(args)}


def _print_model_table(name, res) -> None:
    print(f"\n=== {name} === validation_rows={res['validation_rows']} n_days={res['n_days']} "
          f"label_threshold={res['label_threshold']} cost={res['cost']}")
    print("  thr | sig_cnt cov%   dir_prec wrong%  avg_signed cost_hit% sig/day")
    for t, info in res["thresholds"].items():
        c = info["combined"]
        if c["signal_count"] == 0:
            print(f"  {t} |       0   0.00      -       -          -        -        -")
            continue
        print(f"  {t} | {c['signal_count']:7d} {c['signal_coverage']*100:5.2f} "
              f"{c['directional_precision']:.3f}   {c['wrong_direction_rate']*100:5.2f}  "
              f"{c['avg_signed_return']:+.5f}  {c['cost_label_hit_rate']*100:5.2f}   "
              f"{(c['signals_per_day'] or 0):7.1f}")
    print(f"  best_threshold_by_signed_return_minus_cost: {res['best_threshold_by_signed_return_minus_cost']}")
    for pct, info in res["top_pct"].items():
        c = info["combined"]
        if c["signal_count"] == 0:
            continue
        print(f"  top {float(pct)*100:4.1f}% (floor={info['confidence_floor']}): "
              f"dir_prec={c['directional_precision']:.3f} avg_signed={c['avg_signed_return']:+.5f} "
              f"cost_hit={c['cost_label_hit_rate']*100:.2f}%")


def run(args):
    """Validate, analyze each model, write threshold_analysis.json. Returns ``(analysis, out)``."""
    import joblib  # noqa: PLC0415

    plan = preflight(args)
    if args.dry_run:
        print("DRY_RUN (no analysis, no write)")
        print(f"  dataset: {plan['dataset']}  models: {plan['models']}  out: {plan['out']}")
        return None

    analysis: dict[str, Any] = {"dataset_path": plan["dataset"],
                                "models_dir": plan["models_dir"], "models": {}}
    for name in plan["models"]:
        model = joblib.load(Path(plan["models_dir"]) / name / "model.joblib")
        fcols = json.loads((Path(plan["models_dir"]) / name / "feature_columns.json")
                           .read_text(encoding="utf-8"))
        X, y, fr, ev = load_validation(plan["dataset"], fcols,
                                       max_rows=args.max_validation_rows)
        res = analyze_model(model, X, y, fr, event_time_ns=ev)
        analysis["models"][name] = res
        analysis.setdefault("validation_rows", res["validation_rows"])
        _print_model_table(name, res)

    out = write_analysis(plan["out"], analysis)
    print(f"\nANALYSIS_JSON: {out}")
    return analysis, out


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        run(args)
    except (ValueError, FileExistsError) as exc:
        print(f"PREFLIGHT_ERROR: {exc}")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
