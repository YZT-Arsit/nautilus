#!/usr/bin/env python3
"""CLI to train the LightGBM baseline (B2) on the ML V1 train/validation dataset.

Thin orchestrator over ``research.lightgbm_baseline``: loads ``split=train`` /
``split=validation``, fits one CPU multiclass ``LGBMClassifier``, evaluates
classification + threshold/cost-aware metrics, and writes artifacts (model.joblib
/ metadata.json / metrics.json / threshold_analysis.json / feature_importance.json
/ feature_columns.json + top-level summary.json) under ``outputs/models/``. It
also compares against the B1 LR threshold baseline if available. Reads only the
validation split (never test); no backtest; no nautilus_trader.

Run on the server via ``uv run --no-sync python``::

    uv run --no-sync python scripts/train_lightgbm_baseline.py \
        --dataset outputs/research_datasets/ml_v1_btcusdt_1m_train_val \
        --out outputs/models/ml_v1_btcusdt_1m_lightgbm_baseline --seed 42
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

from research.lightgbm_baseline import (  # noqa: E402
    B1_LR_BASELINE,
    DEFAULT_MIN_SIGNALS,
    DEFAULT_PARAMS,
    DEFAULT_SEED,
    build_metadata,
    compare_to_baseline,
    load_feature_columns,
    train,
    write_artifacts,
)

_ALLOWED_OUT_PREFIX = "outputs/models"
TRAIN_SPLIT, VALIDATION_SPLIT = "train", "validation"


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Train the LightGBM baseline (B2) on the ML V1 dataset")
    p.add_argument("--dataset", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--seed", type=int, default=DEFAULT_SEED)
    p.add_argument("--n-estimators", type=int, default=DEFAULT_PARAMS["n_estimators"])
    p.add_argument("--learning-rate", type=float, default=DEFAULT_PARAMS["learning_rate"])
    p.add_argument("--num-leaves", type=int, default=DEFAULT_PARAMS["num_leaves"])
    p.add_argument("--max-depth", type=int, default=DEFAULT_PARAMS["max_depth"])
    p.add_argument("--min-child-samples", type=int, default=DEFAULT_PARAMS["min_child_samples"])
    p.add_argument("--subsample", type=float, default=DEFAULT_PARAMS["subsample"])
    p.add_argument("--colsample-bytree", type=float, default=DEFAULT_PARAMS["colsample_bytree"])
    p.add_argument("--reg-lambda", type=float, default=DEFAULT_PARAMS["reg_lambda"])
    p.add_argument("--n-jobs", type=int, default=1)
    p.add_argument("--max-train-rows", type=int, default=None)
    p.add_argument("--max-validation-rows", type=int, default=None)
    p.add_argument("--min-signals", type=int, default=DEFAULT_MIN_SIGNALS)
    p.add_argument("--compare-to", default=B1_LR_BASELINE,
                   help="B1 threshold_analysis.json to compare against (skipped if missing)")
    p.add_argument("--no-save-model", action="store_true")
    p.add_argument("--overwrite", action="store_true")
    p.add_argument("--dry-run", action="store_true")
    return p


def params_from_args(args) -> dict[str, Any]:
    return {"learning_rate": args.learning_rate, "n_estimators": args.n_estimators,
            "num_leaves": args.num_leaves, "max_depth": args.max_depth,
            "min_child_samples": args.min_child_samples, "subsample": args.subsample,
            "colsample_bytree": args.colsample_bytree, "reg_lambda": args.reg_lambda}


def preflight(args) -> dict[str, Any]:
    out_posix = Path(args.out).as_posix()
    if "historical_data" in out_posix:
        raise ValueError("refusing to write under historical_data")
    if "outputs/backtests" in out_posix:
        raise ValueError("refusing to write under outputs/backtests")
    if _ALLOWED_OUT_PREFIX not in out_posix:
        raise ValueError(f"output_dir must be under {_ALLOWED_OUT_PREFIX}/")

    ds = Path(args.dataset)
    if not ds.exists():
        raise ValueError(f"dataset dir does not exist: {ds}")
    if not (ds / "feature_columns.json").exists():
        raise ValueError(f"dataset missing feature_columns.json: {ds}")
    for split in (TRAIN_SPLIT, VALIDATION_SPLIT):
        if not (ds / f"split={split}").exists():
            raise ValueError(f"dataset missing split={split}/ under {ds}")

    if not args.dry_run and Path(args.out).exists() and not args.overwrite:
        raise ValueError(f"output_dir already exists (use --overwrite): {args.out}")

    return {"dataset": str(ds), "out": args.out, "seed": args.seed,
            "params": params_from_args(args), "n_jobs": args.n_jobs,
            "save_model": not args.no_save_model, "min_signals": args.min_signals,
            "max_train_rows": args.max_train_rows, "max_validation_rows": args.max_validation_rows,
            "compare_to": args.compare_to}


def _load_comparison(path):
    if not path:
        return None
    p = Path(path)
    if not p.exists():
        return None
    return json.loads(p.read_text(encoding="utf-8"))


def run(args):
    """Validate, train, write. Returns ``(summary, out_path)`` or ``None`` for dry-run."""
    plan = preflight(args)
    feature_columns = load_feature_columns(plan["dataset"])

    if args.dry_run:
        print("DRY_RUN (no train, no write)")
        print(f"  dataset: {plan['dataset']}  out: {plan['out']}  seed={plan['seed']}")
        print(f"  params: {plan['params']}  features: {len(feature_columns)}")
        print(f"  compare_to: {plan['compare_to']}")
        return None

    result = train(plan["dataset"], params=plan["params"], seed=plan["seed"],
                   n_jobs=plan["n_jobs"], max_train_rows=plan["max_train_rows"],
                   max_validation_rows=plan["max_validation_rows"])
    metadata = build_metadata(dataset_path=plan["dataset"], feature_columns=feature_columns,
                              used_params=result["used_params"], seed=plan["seed"],
                              command_args={k: v for k, v in vars(args).items()})
    comparison = compare_to_baseline(result["threshold_analysis"],
                                     _load_comparison(plan["compare_to"]))
    _, summary = write_artifacts(plan["out"], result, metadata, save_model=plan["save_model"],
                                 comparison=comparison, min_signals=plan["min_signals"],
                                 dataset_path=plan["dataset"])

    m = result["metrics"]
    print(f"[lightgbm] acc={m['accuracy']:.4f} bal_acc={m['balanced_accuracy']:.4f} "
          f"macro_f1={m['macro_f1']:.4f} pred_dist={m['prediction_distribution']}")
    thr = result["threshold_analysis"]
    print(f"best_threshold_by_signed_return_minus_cost: {thr['best_threshold_by_signed_return_minus_cost']}")
    if comparison is not None:
        print(f"beats_b1_threshold_baseline: {comparison['beats_baseline']} "
              f"(b2 thr={comparison['b2_best_threshold']} vs b1 thr={comparison['baseline_best_threshold']})")
    print(f"OUTPUT_DIR: {plan['out']}  train_rows={result['train_rows']} "
          f"validation_rows={result['validation_rows']}")
    return summary, Path(plan["out"])


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
