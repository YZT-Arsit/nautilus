#!/usr/bin/env python3
"""CLI to train sklearn baselines (B1) on the ML V1 train/validation dataset.

Thin orchestrator over ``research.sklearn_baseline``: it loads the parquet
dataset's ``split=train`` / ``split=validation``, trains the requested sklearn
models, evaluates on validation, and writes per-model artifacts + a top-level
``summary.json``. Output is restricted to ``outputs/models/`` (never
``historical_data/`` or ``outputs/backtests/``). The **test** split is never read.
No LightGBM (that is B2), no backtest, no nautilus_trader.

Run on the server via ``uv run --no-sync python`` so the ``.venv`` sklearn (which
is not in ``uv.lock``) is used and not uninstalled by an implicit ``uv sync``::

    uv run --no-sync python scripts/train_sklearn_baseline.py \
        --dataset outputs/research_datasets/ml_v1_btcusdt_1m_train_val \
        --out outputs/models/ml_v1_btcusdt_1m_sklearn_baseline \
        --models logistic_regression,hist_gradient_boosting --seed 42

``--dry-run`` validates args + prints the plan and trains/writes nothing.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

# Repo-root bootstrap: running ``python scripts/train_sklearn_baseline.py`` puts
# ``scripts/`` (not the repo root) on sys.path[0], so the top-level ``research``
# package would not import. Insert the repo root first.
_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from research.sklearn_baseline import (  # noqa: E402
    DEFAULT_MODELS,
    DEFAULT_SEED,
    build_metadata,
    load_feature_columns,
    load_split,
    train_one,
    write_model_artifact,
    write_summary,
)

VALID_MODELS = ("logistic_regression", "hist_gradient_boosting")
_ALLOWED_OUT_PREFIX = "outputs/models"
TRAIN_SPLIT = "train"
VALIDATION_SPLIT = "validation"


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Train sklearn baselines (B1) on the ML V1 dataset")
    p.add_argument("--dataset", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--models", default=",".join(DEFAULT_MODELS))
    p.add_argument("--seed", type=int, default=DEFAULT_SEED)
    p.add_argument("--max-train-rows", type=int, default=None)
    p.add_argument("--max-validation-rows", type=int, default=None)
    p.add_argument("--n-jobs", type=int, default=1)
    p.add_argument("--no-save-model", action="store_true")
    p.add_argument("--overwrite", action="store_true")
    p.add_argument("--dry-run", action="store_true")
    return p


def parse_models(raw: str) -> list[str]:
    return [m.strip() for m in str(raw).split(",") if m.strip()]


def preflight(args: argparse.Namespace) -> dict[str, Any]:
    """Validate args (raises ValueError on any violation). Returns the resolved plan."""
    models = parse_models(args.models)
    if not models:
        raise ValueError("models must be non-empty")
    bad = [m for m in models if m not in VALID_MODELS]
    if bad:
        raise ValueError(f"invalid models {bad}; allowed: {VALID_MODELS}")

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

    return {"models": models, "dataset": str(ds), "out": args.out, "seed": args.seed,
            "n_jobs": args.n_jobs, "save_model": not args.no_save_model,
            "max_train_rows": args.max_train_rows,
            "max_validation_rows": args.max_validation_rows}


def run(args: argparse.Namespace):
    """Validate, train, write. Returns ``(summary_dict, out_path)`` or ``None`` for dry-run."""
    plan = preflight(args)
    feature_columns = load_feature_columns(plan["dataset"])

    if args.dry_run:
        print("DRY_RUN (no train, no write)")
        print(f"  dataset: {plan['dataset']}  out: {plan['out']}")
        print(f"  models: {plan['models']}  seed={plan['seed']} "
              f"n_jobs={plan['n_jobs']} save_model={plan['save_model']}")
        print(f"  features: {len(feature_columns)}  "
              f"max_train_rows={plan['max_train_rows']} max_validation_rows={plan['max_validation_rows']}")
        return None

    X_tr, y_tr, n_tr = load_split(plan["dataset"], TRAIN_SPLIT, feature_columns,
                                  max_rows=plan["max_train_rows"])
    X_val, y_val, n_val = load_split(plan["dataset"], VALIDATION_SPLIT, feature_columns,
                                     max_rows=plan["max_validation_rows"])

    out = Path(plan["out"])
    out.mkdir(parents=True, exist_ok=True)
    command_args = {k: v for k, v in vars(args).items()}

    results: dict[str, dict] = {}
    for name in plan["models"]:
        model, metrics = train_one(name, X_tr, y_tr, X_val, y_val, feature_columns,
                                   seed=plan["seed"], n_jobs=plan["n_jobs"])
        metadata = build_metadata(name, dataset_path=plan["dataset"],
                                  feature_columns=feature_columns,
                                  train_split=TRAIN_SPLIT, validation_split=VALIDATION_SPLIT,
                                  seed=plan["seed"], command_args=command_args)
        write_model_artifact(out, name, model, metadata, metrics, feature_columns,
                             save_model=plan["save_model"])
        results[name] = metrics
        print(f"[{name}] acc={metrics['accuracy']:.4f} bal_acc={metrics['balanced_accuracy']:.4f} "
              f"macro_f1={metrics['macro_f1']:.4f} pred_dist={metrics['prediction_distribution']}")

    summary = write_summary(out, results, dataset_path=plan["dataset"],
                            feature_columns=feature_columns, train_rows=n_tr,
                            validation_rows=n_val)
    print(f"OUTPUT_DIR: {out}")
    print(f"train_rows: {n_tr}  validation_rows: {n_val}  features: {len(feature_columns)}")
    print(f"summary_json: {summary}")
    return json.loads(Path(summary).read_text(encoding="utf-8")), out


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
