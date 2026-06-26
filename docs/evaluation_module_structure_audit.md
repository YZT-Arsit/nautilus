# Evaluation / Reporting Module Structure Audit (Small-Module Principle)

This audits the strategy-**evaluation / reporting** code only. Scope: do the
metric/table builders follow a small-module structure (thin CLI, pure metric
functions, reusable library), and is evaluation logic kept out of `strategy` /
`feature_engine` / `data_engine` / the nautilus backend? It also records the
minimal refactor actually performed this phase.

## 1. Evaluation code inventory (before this phase)

| File | Role | Lines | Issue |
|---|---|---:|---|
| `scripts/build_crypto_perpetual_eval_table.py` | single/matrix-cell eval row + CSV/MD + CLI | ~553 | Pure metric math lived **inside a CLI script**; reused by other scripts via `import scripts.build_crypto_perpetual_eval_table as base` (a CLI imported as a library). |
| `scripts/build_crypto_perpetual_matrix_eval_table.py` | matrix aggregation + ranking + CLI | ~226 | OK-sized, but depends on the CLI script above for all math. |
| `scripts/build_strategy_batch_eval_table.py` | batch pivot table + CLI | ~480 | **Heaviest.** Mixed metric math + cell I/O + pivot orientation + writers + a coverage-audit dict + CLI in one file. Also imported the single-builder CLI as a library. Orientation was rows=metric/cols=symbol (wrong per the boss spec). |
| `scripts/run_vwm_batch_backtests.py` | batch planning / inventory / run / summary | ~1150 | Out of evaluation scope (it is the *runner*); not a reporting module. Left unchanged except prior bug fixes. |
| tests: `test_crypto_perpetual_eval_table.py`, `test_crypto_perpetual_matrix_eval_table.py`, `test_strategy_batch_eval_table.py` | coverage of the above | — | Reference `base.*` names, so the refactor must preserve them. |

## 2. Findings against the small-module principle

**A. CLI layer** — Acceptable: each `main()` only parses args, resolves paths,
calls library functions, writes outputs, prints a summary. No heavy logic in
`argparse` handlers.

**B. Metric-calculation layer** — *Violation.* The pure math (returns, risk,
fee scenarios, exposure, trade quality, daily stats, payoff/expectancy, …) was
embedded in `scripts/*.py` CLI modules and **duplicated** between the single
builder and the batch builder. The batch builder reached into the single
builder's private helpers (`base._finite`, `base._read_csv_rows`,
`base._annualized_return`, …) — a CLI used as a shared library.

**C. Module location** — There was no importable evaluation library. The natural
home is the existing `research/` package (already stdlib-only, Nautilus-free,
explicitly separate from `feature_engine`).

**D. Orientation** — The batch table was rows=metric / cols=symbol; the boss
deliverable requires rows=symbol / cols=metric.

**No leakage found:** none of the evaluation code imported `strategy`,
`feature_engine/features`, `data_engine`, or `nautilus_trader`. That boundary was
already clean and is preserved.

## 3. Logic that should be extracted into small functions

- returns: annualized, benchmark/excess, fee scenarios, daily best/worst/avg/std
- risk: vol / Sharpe / Sortino / abs-max-DD, downside vol
- trade quality: gross split, median/best/worst, max consec win/loss, payoff, expectancy
- exposure: long/short/flat share, holding time/bars, direction bias
- cost: commission ratios, net/gross, break-even, turnover
- relative: benchmark direction
- table layer: column schemas, per-symbol row assembly, CSV/MD writers, coverage audit

## 4. Which CLIs are kept

All three CLIs are kept (each is a thin entry point). The single + matrix builders
are retained as the **internal analysis** path (per the boss note that the old
matrix view stays for internal use); the batch builder is the boss deliverable and
was reoriented to rows=symbol.

## 5. Recommended minimal refactor

Introduce two small library modules under `research/` and have the CLIs compose
them; do **not** over-split into many tiny files.

```
research/evaluation_metrics.py   # pure math, stdlib only (single source of truth)
research/evaluation_tables.py    # rows=symbol table assembly + writers + coverage audit
```

## 6. Refactor actually performed this phase

- **Added** `research/evaluation_metrics.py` — the single home for all metric
  math (pure, stdlib `math`/`statistics`/`datetime` only; no disk, no network, no
  pyarrow). 20+ functions covering returns/risk/trade-quality/exposure/cost/relative.
- **Added** `research/evaluation_tables.py` — rows=symbol assembly
  (`build_symbol_row` / `missing_data_row` / `failed_row`), CSV/MD writers, the
  `SYMBOL_METRIC_COLUMNS` (86) / `MD_CORE_COLUMNS` schemas, and the per-metric
  `METRIC_AUDIT` + coverage writers. Disk I/O limited to small reporting readers;
  pyarrow imported lazily *inside* `read_benchmark_closes` only.
- **Rewrote** `scripts/build_strategy_batch_eval_table.py` as a thin CLI over the
  two modules and **flipped orientation to rows=symbol / cols=metric**.
- **Rewired** `scripts/build_crypto_perpetual_eval_table.py` so its metric math is
  imported from `research.evaluation_metrics` (re-exported under the historical
  names: `_finite`, `_days`, `fee_scenarios`, `equity_stats`, …). Behaviour is
  byte-for-byte identical (same function bodies, relocated); the duplicate copies
  are gone. The matrix builder rides on it unchanged.
- **Tests:** new `nautilus_ext/tests/test_evaluation_metrics.py`; rewrote
  `nautilus_ext/tests/test_strategy_batch_eval_table.py` for rows=symbol. The
  existing single/matrix builder tests pass unchanged because every `base.*` name
  they use is preserved.

Result: metric math has **one** home; both the single/matrix (internal) and batch
(boss) tables now compose it; the batch table is correctly oriented.

## 7. Boundaries preserved (verified)

- **VWM strategy math:** untouched (no edits under `strategy/` or the VWM signal
  modules).
- **`feature_engine` / `data_engine`:** structure untouched; not imported by any
  evaluation module.
- **No new lightweight backtester**, no registry rework, no CFFEX, no funding-aware
  PnL added.
- Evaluation modules import only stdlib + `research.*` (AST-checked in tests);
  `pyproject.toml` / `uv.lock` unchanged; no dependency install.
