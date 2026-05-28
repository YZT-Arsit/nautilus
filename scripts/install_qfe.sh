#!/usr/bin/env bash
# One-command install for quant_feature_engine on POSIX (macOS/Linux).
#
# Mirror of scripts/install_qfe.ps1. From a fresh checkout, brings the
# environment to "39/39 tests green" with one invocation.
#
# Usage:
#   ./scripts/install_qfe.sh
#   ./scripts/install_qfe.sh --skip-tests
#   ./scripts/install_qfe.sh --python /usr/local/bin/python3
#   ./scripts/install_qfe.sh --upgrade
#   QFE_PYTHON=/path/to/python ./scripts/install_qfe.sh
#
# Environment:
#   QFE_PYTHON   - explicit Python interpreter to use (overrides discovery).

set -euo pipefail

PYTHON=""
UPGRADE=0
SKIP_TESTS=0

while [[ $# -gt 0 ]]; do
    case "$1" in
        --python)       PYTHON="$2"; shift 2 ;;
        --upgrade)      UPGRADE=1; shift ;;
        --skip-tests)   SKIP_TESTS=1; shift ;;
        -h|--help)
            grep -E "^# " "$0" | sed -e 's/^# //'
            exit 0
            ;;
        *)              echo "Unknown arg: $1" >&2; exit 2 ;;
    esac
done

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
qfe_dir="$repo_root/quant_feature_engine"
lock_file="$qfe_dir/requirements.lock.txt"
req_file="$qfe_dir/requirements.txt"

resolve_python() {
    if [[ -n "$PYTHON" && -x "$PYTHON" ]]; then echo "$PYTHON"; return; fi
    if [[ -n "${QFE_PYTHON:-}" && -x "$QFE_PYTHON" ]]; then echo "$QFE_PYTHON"; return; fi
    if [[ -x "$repo_root/.venv/bin/python" ]]; then echo "$repo_root/.venv/bin/python"; return; fi
    echo "No Python interpreter found. Provide --python, set QFE_PYTHON, or create a .venv at $repo_root/.venv." >&2
    exit 1
}

python="$(resolve_python)"
echo "Python: $python"
"$python" -V

# Pick input file.
if [[ -f "$lock_file" ]]; then
    src="$lock_file"
    echo "Installing from lockfile: $lock_file"
elif [[ -f "$req_file" ]]; then
    src="$req_file"
    echo "WARN: requirements.lock.txt not found; falling back to requirements.txt. Versions may drift." >&2
else
    echo "Neither $lock_file nor $req_file exists; cannot install." >&2
    exit 1
fi

pip_args=(-m pip install -r "$src")
if [[ "$UPGRADE" -eq 1 ]]; then pip_args+=(--upgrade); fi
echo
echo "> $python ${pip_args[*]}"
"$python" "${pip_args[@]}"

# Verify imports.
echo
echo "Verifying imports ..."
"$python" - <<'PY'
import sys
mods = ['polars', 'pyarrow', 'yaml', 'pytest']
fail = []
for m in mods:
    try:
        mod = __import__(m)
        print(f"  ok  {m:<10} {getattr(mod, '__version__', '?')}")
    except Exception as e:
        print(f"  ERR {m:<10} {e}")
        fail.append(m)
sys.exit(1 if fail else 0)
PY

if [[ "$SKIP_TESTS" -eq 0 ]]; then
    echo
    echo "Running pytest suite ..."
    cd "$repo_root"
    "$python" -m pytest quant_feature_engine/tests -q
fi

echo
echo "OK quant_feature_engine install + smoke test complete."
