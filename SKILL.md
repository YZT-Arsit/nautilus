# Skill: Local Codex Remote Windows Server Test for Nautilus Project

## Purpose

You are a local Codex agent working on the `nautilus_trader` project.  
You may modify code locally, then connect to a remote Windows server to synchronize the project and run tests or backtests against real market data.

This skill is not limited to one strategy or one refactor task. Use it for:

- Strategy refactoring
- Adding new strategy signal engines
- Modifying `BaseBarStrategy`
- Modifying data connector / adapter / runner logic
- Running real-data backtests
- Running focused tests
- Checking runtime errors that require the server's real data environment

## Remote Server

Remote Windows server information:

```text
Host: 172.16.112.81
User: quant_data
Password: Y@ng
Project path: D:\nautilus
Shell: PowerShell or cmd
````

## Verified Market Data Catalog

Use the following server-side catalog path for real-data checks:

```text
D:\QuanHub\DataHome\DataTrans\nautilus_catalog
```

Verified on the remote server:

```text
cffex_l1_quote\data\quote_tick\IH2303.CFFEX\...
cffex_l1_quote\data\futures_contract\IH2303.CFFEX\...
cffex_l1_depth10\data\order_book_depths\IH2303.CFFEX\...
```

The current catalog contains QuoteTick, order book depth, and futures-contract
metadata parquet data. It does not currently contain ready-to-run OHLCV Bar
parquet data. Bar-based strategies such as VWM must use a connector or
preprocessing step to aggregate QuoteTick data into bars first. Any volume
derived from quotes is synthetic and is suitable only for engineering
validation, not formal performance conclusions.

Before any remote operation, verify connectivity:

```powershell
ssh quant_data@172.16.112.81 "hostname"
```

Then verify the project path:

```powershell
ssh quant_data@172.16.112.81 "powershell -NoProfile -ExecutionPolicy Bypass -Command ""cd D:\nautilus; pwd; git status --short"""
```

If SSH fails, do not guess. Report the exact error.

---

## Local / Remote Roles

### Local side

Local Codex may:

* Inspect and edit project code.
* Run lightweight local tests if dependencies are available.
* Generate patches.
* Commit changes to a branch if requested.
* Sync changes to the remote server for real-data validation.

### Remote server side

Remote server is used for:

* Running the project inside `D:\nautilus`.
* Accessing real data paths configured in `internal_examples\run_user_strategies.py`.
* Running backtests against real data.
* Producing logs and reports under project output directories.

Do not assume the remote environment is identical to local. Always check Python/uv availability before running large commands.

---

## Standard Remote Environment Checks

Run these first when starting a new task:

```powershell
ssh quant_data@172.16.112.81 "powershell -NoProfile -ExecutionPolicy Bypass -Command ""cd D:\nautilus; git status --short"""
```

Check Python:

```powershell
ssh quant_data@172.16.112.81 "powershell -NoProfile -ExecutionPolicy Bypass -Command ""cd D:\nautilus; python --version"""
```

Check uv if relevant:

```powershell
ssh quant_data@172.16.112.81 "powershell -NoProfile -ExecutionPolicy Bypass -Command ""cd D:\nautilus; uv --version"""
```

Check project import:

```powershell
ssh quant_data@172.16.112.81 "powershell -NoProfile -ExecutionPolicy Bypass -Command ""cd D:\nautilus; python -c 'import nautilus_ext; print(\"import ok\")'"""
```

If `uv` exists, prefer:

```powershell
uv run python ...
uv run pytest ...
```

Otherwise use:

```powershell
python ...
pytest ...
```

---

## Recommended Sync Modes

Choose one sync mode depending on the task.

---

### Mode A: Git-based sync, preferred when repo is clean

Use this when both local and remote are Git clones of the same repository.

Local:

```powershell
git status
git checkout -b codex/<task-name>
```

After edits:

```powershell
git diff
git add .
git commit -m "Refactor strategy framework"
git push origin codex/<task-name>
```

Remote:

```powershell
ssh quant_data@172.16.112.81 "powershell -NoProfile -ExecutionPolicy Bypass -Command ""cd D:\nautilus; git fetch origin; git checkout codex/<task-name>; git pull"""
```

Then run remote tests/backtest.

Use this mode for larger or multi-file changes.

---

### Mode B: Patch-based sync, preferred before committing

Use this when the user wants to test changes remotely before committing.

Local:

```powershell
git diff > codex_remote_test.patch
```

Copy patch to server:

```powershell
scp codex_remote_test.patch quant_data@172.16.112.81:D:/nautilus/codex_remote_test.patch
```

Remote apply:

```powershell
ssh quant_data@172.16.112.81 "powershell -NoProfile -ExecutionPolicy Bypass -Command ""cd D:\nautilus; git status --short; git apply --check codex_remote_test.patch; git apply codex_remote_test.patch"""
```

After test, if needed, revert remote patch:

```powershell
ssh quant_data@172.16.112.81 "powershell -NoProfile -ExecutionPolicy Bypass -Command ""cd D:\nautilus; git restore ."""
```

Use this mode when you want a temporary remote test without pushing a branch.

---

### Mode C: Direct remote editing, use only when explicitly requested

Use SSH to edit or generate files directly under `D:\nautilus`.

Avoid this mode unless necessary. Git-based or patch-based sync is easier to audit.

---

## Command Quoting Rules for Windows SSH

Remote commands should usually use this form:

```powershell
ssh quant_data@172.16.112.81 "powershell -NoProfile -ExecutionPolicy Bypass -Command ""cd D:\nautilus; <COMMAND>"""
```

Examples:

```powershell
ssh quant_data@172.16.112.81 "powershell -NoProfile -ExecutionPolicy Bypass -Command ""cd D:\nautilus; git status --short"""
```

```powershell
ssh quant_data@172.16.112.81 "powershell -NoProfile -ExecutionPolicy Bypass -Command ""cd D:\nautilus; python internal_examples\run_user_strategies.py"""
```

```powershell
ssh quant_data@172.16.112.81 "powershell -NoProfile -ExecutionPolicy Bypass -Command ""cd D:\nautilus; uv run python internal_examples\run_user_strategies.py"""
```

---

## Project Architecture Rules

Do not collapse the current strategy architecture.

Current expected architecture:

```text
internal_examples/run_user_strategies.py
    -> NautilusStrategySpec
    -> StrategyTemplate
    -> strategy_registry.build_signal_engine
    -> BaseBarStrategy
    -> concrete signal engine
    -> SignalResult
    -> BaseBarStrategy.execute_signal
```

Layer responsibilities:

### `run_user_strategies.py`

User-facing configuration only.

It may contain:

* `DATA_ROOT`
* `SYMBOL`
* `INSTRUMENT_TYPE`
* `VENUE`
* `STARTING_BALANCE`
* `USER_STRATEGIES`

It should not contain concrete strategy logic.

### `StrategyTemplate`

Thin adapter only.

It should:

* Read `strategy_kind`.
* Call `build_signal_engine(strategy_kind, params)`.
* Pass the signal engine into `BaseBarStrategy`.

It should not import concrete strategy classes directly.

### `strategy_registry.py`

Signal engine registry.

It should:

* Register signal engine factories.
* Build signal engines by `strategy_kind`.
* Provide clear errors for unknown `strategy_kind`.

Adding a new strategy should usually require only:

* New `xxx_signals.py`
* Registry entry
* User params in `run_user_strategies.py`

### `BaseBarStrategy`

Common Nautilus glue.

It owns:

* `on_start`
* `subscribe_bars`
* `on_bar`
* Bar validation
* `Bar -> BarInput`
* Current position lookup
* Bars-since-entry tracking
* `SignalResult -> order execution`

It should not contain VWM-specific conditions.

### `signal_types.py`

Common contracts:

* `BarInput`
* `SignalResult`

### Concrete signal modules

Example:

```text
nautilus_ext/strategies/vwm_short_signals.py
```

Concrete signal modules should:

* Receive `BarInput`, `position`, `bars_since_entry`.
* Return `SignalResult`.
* Not create Nautilus orders directly.
* Not access portfolio directly.
* Not depend on data files directly.

---

## General Strategy Development Workflow

When adding or modifying a strategy:

1. Keep framework files stable unless the change is truly reusable.
2. Put strategy-specific rules into `xxx_signals.py`.
3. Use `SignalResult` as the output contract.
4. Register the strategy in `strategy_registry.py`.
5. Configure it in `run_user_strategies.py`.
6. Run local static checks if possible.
7. Sync to server.
8. Run real-data test on server.
9. Summarize changed files, command output, and remaining issues.

---

## VWM Strategy-Specific Notes

Current VWM short strategy maps from TradeBlazer:

```text
VWM = XAverage(Vol * Momentum(Close, MomLen), AvgLen)
AATR = AvgTrueRange(ATRLen)

BullSetup = CrossOver(VWM, 0)
BearSetup = CrossUnder(VWM, 0)

If BearSetup:
    SSetup = 0
    SEPrice = Close
Else:
    SSetup = SSetup[1] + 1

Entry:
    MarketPosition == 0
    Low <= SEPrice[1] - ATRPcnt * AATR[1]
    SSetup[1] <= SetupLen
    SSetup >= 1
    Vol > 0

Exit:
    MarketPosition == -1
    BarsSinceEntry > 0
    Vol > 0
    BullSetup[1] == True
```

Preserve `[1]` semantics:

* Entry trigger uses previous `SEPrice`.
* Entry trigger uses previous `ATR`.
* Setup-window check uses previous `SSetup`.
* Exit uses previous `BullSetup`.

Reuse Nautilus indicators when available:

```python
from nautilus_trader.indicators import ExponentialMovingAverage
from nautilus_trader.indicators import AverageTrueRange
```

Do not replace raw TradeBlazer-style momentum with Nautilus `RateOfChange`, because `RateOfChange` is not `Close[t] - Close[t-N]`.

---

## Current Optional Refactor Direction for VWM

If asked to shorten `vwm_short_signals.py`, prefer extracting VWM components into:

```text
nautilus_ext/strategies/vwm_short_components.py
```

Possible contents:

```text
VwmShortSignalConfig
VwmShortSnapshot
VwmShortIndicators
```

The goal is for `vwm_short_signals.py` to keep only the high-level signal flow:

```text
validate bar
snapshot previous state
update indicators
calculate bull_setup / bear_setup
update SEPrice and SSetup
calculate entry / exit / cancel
return SignalResult
```

Do not change behavior while refactoring.

---

## Remote Test Commands

### Basic real-data backtest

Try uv first if available:

```powershell
ssh quant_data@172.16.112.81 "powershell -NoProfile -ExecutionPolicy Bypass -Command ""cd D:\nautilus; uv run python internal_examples\run_user_strategies.py"""
```

Fallback:

```powershell
ssh quant_data@172.16.112.81 "powershell -NoProfile -ExecutionPolicy Bypass -Command ""cd D:\nautilus; python internal_examples\run_user_strategies.py"""
```

### Focused tests

```powershell
ssh quant_data@172.16.112.81 "powershell -NoProfile -ExecutionPolicy Bypass -Command ""cd D:\nautilus; uv run pytest tests -k 'vwm_short or tradeblazer or strategy_registry'"""
```

Fallback:

```powershell
ssh quant_data@172.16.112.81 "powershell -NoProfile -ExecutionPolicy Bypass -Command ""cd D:\nautilus; pytest tests -k 'vwm_short or tradeblazer or strategy_registry'"""
```

### Check generated outputs

```powershell
ssh quant_data@172.16.112.81 "powershell -NoProfile -ExecutionPolicy Bypass -Command ""cd D:\nautilus; dir outputs\user_strategies"""
```

### Show latest report directories

```powershell
ssh quant_data@172.16.112.81 "powershell -NoProfile -ExecutionPolicy Bypass -Command ""cd D:\nautilus; Get-ChildItem outputs\user_strategies | Sort-Object LastWriteTime -Descending | Select-Object -First 5"""
```

---

## Remote Debug Commands

Check Git status:

```powershell
ssh quant_data@172.16.112.81 "powershell -NoProfile -ExecutionPolicy Bypass -Command ""cd D:\nautilus; git status --short"""
```

Check branch:

```powershell
ssh quant_data@172.16.112.81 "powershell -NoProfile -ExecutionPolicy Bypass -Command ""cd D:\nautilus; git branch --show-current"""
```

Check Python executable:

```powershell
ssh quant_data@172.16.112.81 "powershell -NoProfile -ExecutionPolicy Bypass -Command ""cd D:\nautilus; python -c 'import sys; print(sys.executable); print(sys.version)'"""
```

Check imports:

```powershell
ssh quant_data@172.16.112.81 "powershell -NoProfile -ExecutionPolicy Bypass -Command ""cd D:\nautilus; python -c 'from nautilus_ext.strategies.strategy_registry import available_signal_engines; print(available_signal_engines())'"""
```

Check data path from runner:

```powershell
ssh quant_data@172.16.112.81 "powershell -NoProfile -ExecutionPolicy Bypass -Command ""cd D:\nautilus; python -c 'import internal_examples.run_user_strategies as r; print(r.DATA_ROOT); print(r.SYMBOL)'"""
```

---

## Error Handling

If a remote command fails:

1. Preserve the exact command.
2. Preserve the exact error output.
3. Identify whether the failure is:

   * SSH/network issue
   * Python environment issue
   * Import/dependency issue
   * Data path issue
   * Strategy logic issue
   * Nautilus runtime issue
4. Do not hide failures.
5. Do not claim the test passed unless the command completed successfully.

---

## Reporting Format

After every remote validation, report:

```text
Task:
- ...

Local files changed:
- ...

Sync method:
- Git branch / patch / direct remote

Remote commands run:
- ...

Remote result:
- success / failed

Important output:
- run_id:
- strategy_name:
- bars_count:
- report_dir:
- metrics:
- errors:

Next recommended action:
- ...
```

---

## Security / Data Handling Notes

Do not copy raw market data from the remote server unless explicitly requested.

Do not commit raw data, generated reports, or local environment files.

Do not put credentials, SSH keys, IP screenshots, or passwords into Git.

Generated reports should stay under project output directories such as:

```text
D:\nautilus\outputs
```

---

## Success Criteria

A task is considered validated only if at least one of the following succeeds on the remote server:

```text
1. Real-data runner succeeds:
   python internal_examples\run_user_strategies.py

2. Relevant tests pass:
   pytest tests -k "..."

3. A narrower diagnostic command proves the target code path works.
```

If the real-data runner is too slow, run a narrower test first, then report that full real-data validation was not completed.
