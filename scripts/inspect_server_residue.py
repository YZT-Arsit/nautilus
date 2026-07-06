#!/usr/bin/env python3
"""READ-ONLY server residue inventory + reversible archive (never deletes).

Phase: obsolete-branch residue cleanup. Default is DRY-RUN: it writes an inventory,
a reference check, and a cleanup plan under ``outputs/cleanup_inventory/`` and moves
nothing. With ``--apply`` it MOVES (never ``rm``) an explicit, hard-coded allowlist
of clearly-inert residue into ``outputs/archive/server_obsolete_branch_residue/``
(preserving relative paths) and writes an ``archive_manifest.csv``.

Hard safety rails:
* Only the explicit ``_SAFE_ARCHIVE`` allowlist is ever moved.
* Nothing under ``historical_data/``, ``outputs/`` (backtests/batches/deliverables/
  architecture_inventory/archive), ``.git``, ``.venv``, ``pyproject.toml``,
  ``uv.lock``, or any git-tracked path is ever moved.
* Existing archive targets are never overwritten (timestamp suffix on collision).
* No ``git`` mutation, no delete, no download, no install.
"""
from __future__ import annotations

import argparse
import csv
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
_OUT = _REPO / "outputs" / "cleanup_inventory"
_ARCHIVE = _REPO / "outputs" / "archive" / "server_obsolete_branch_residue"

# Top-level dirs/files that are heavy or must be preserved -> summarized as keep,
# never descended for residue.
_KEEP_TOPLEVEL = {
    ".git", ".venv", "nautilus_trader", "target", "crates", "historical_data",
    "python", "schema", "tests", "docs", "examples", "pyproject.toml", "uv.lock",
    "Cargo.lock", "Cargo.toml", "README.md", "run_strategy.py", "run_batch.py",
    "run_2y_batch.py", "data_engine", "feature_engine", "strategy_framework",
    "strategies", "scripts", "configs", "tests_platform", "build.py",
}
_KEEP_OUTPUTS = {  # valid / active outputs — never a candidate
    "architecture_inventory", "archive", "backtests", "batches", "deliverables",
    "cleanup_inventory",
}

# EXPLICIT allowlist actually moved on --apply (relative to repo root). Everything
# else is keep or manual_review, regardless of heuristics.
_SAFE_ARCHIVE = [
    "__tmp_c1b_rerun__", "__tmp_c1b_upload__", "__tmp_c2c_fix__", "__tmp_c2c_upload__",
    "nautilus_code_sync.tgz", "_verify_env.py", "_smoke.py",
]


def _run(cmd: list[str]) -> str:
    try:
        return subprocess.run(cmd, cwd=str(_REPO), capture_output=True, text=True,
                              timeout=120).stdout
    except Exception:
        return ""


def _tracked_set() -> set[str]:
    return {ln.replace("\\", "/") for ln in _run(["git", "ls-files"]).splitlines() if ln}


def _untracked_toplevel() -> set[str]:
    out = set()
    for ln in _run(["git", "status", "--short"]).splitlines():
        if ln.startswith("?? "):
            out.add(ln[3:].replace("\\", "/").split("/")[0].rstrip("/"))
    return out


def _dir_size(p: Path) -> tuple[int, int]:
    total = files = 0
    for root, _dirs, fs in os.walk(p):
        for f in fs:
            try:
                total += (Path(root) / f).stat().st_size
                files += 1
            except OSError:
                pass
    return total, files


def _grep_count(name: str, suffix: str) -> int:
    """Count tracked files of a given suffix that reference ``name`` (git grep)."""
    out = _run(["git", "grep", "-l", name, "--", f"*{suffix}"])
    return len([x for x in out.splitlines() if x])


def _mtime(p: Path) -> str:
    try:
        return datetime.fromtimestamp(p.stat().st_mtime, timezone.utc).strftime("%Y-%m-%d %H:%M")
    except OSError:
        return ""


def _classify(name: str, tracked_ct: int) -> tuple[str, bool, str, str, str]:
    """-> (category, cleanup_candidate, risk, action, reason)."""
    if name in _SAFE_ARCHIVE:
        cat = "temp" if name.startswith(("_", "nautilus_code")) else "temp"
        if name.startswith("__tmp"):
            cat = "temp"
        return (cat, True, "low", "archive_candidate", "temp/staging residue, unreferenced")
    if name in ("quant_feature_engine", "internal_examples"):
        return ("obsolete_branch_residue", True, "medium", "manual_review",
                "pre-reorg module; referenced only in markdown docs (count>0)")
    if name == "results":
        return ("unknown", True, "medium", "manual_review", "untracked dir of unknown content")
    if name in ("__pycache__", ".pytest_cache"):
        return ("cache", True, "low", "manual_review",
                "cache; deferred while batch workers run")
    if name == "launch_batch.ps1":
        return ("temp", True, "low", "manual_review", "batch launcher; defer until batch done")
    return ("unknown", True, "medium", "manual_review", "untracked; not on allowlist")


def build_inventory(tracked: set[str], untracked_top: set[str]):
    inv, plan, refs = [], [], []
    ref_cache: dict[str, tuple[int, int]] = {}

    def add_inv(path, typ, size, files, tracked_ct, cat, cand, reason, risk, action, notes):
        inv.append({
            "path": path, "type": typ, "size_bytes": size, "mtime": _mtime(_REPO / path),
            "git_status": "untracked" if path.split("/")[0] in untracked_top else "tracked/other",
            "tracked_by_current_branch": tracked_ct > 0, "ignored_by_git": "",
            "top_level_dir": path.split("/")[0], "likely_category": cat,
            "cleanup_candidate": cand, "cleanup_reason": reason, "risk_level": risk,
            "action": action, "notes": notes + (f" files={files}" if typ == "dir" else ""),
        })

    # repo-root top-level entries
    for entry in sorted(os.listdir(_REPO)):
        p = _REPO / entry
        rel = entry
        is_dir = p.is_dir()
        tracked_ct = sum(1 for t in tracked if t == rel or t.startswith(rel + "/"))
        if entry in _KEEP_TOPLEVEL:
            add_inv(rel, "dir" if is_dir else "file", "", "", tracked_ct,
                    "current_source" if is_dir else "current_config", False,
                    "must-keep (tracked/vendored/active)", "high", "keep", "")
            continue
        size, files = _dir_size(p) if is_dir else (p.stat().st_size, 1)
        cat, cand, risk, action, reason = _classify(entry, tracked_ct)
        if tracked_ct > 0:
            action, cand, reason = "keep", False, "tracked by current branch"
        add_inv(rel, "dir" if is_dir else "file", size, files, tracked_ct,
                cat, cand, reason, risk, action, "")
        # reference check + plan row for candidates
        if cand and action in ("archive_candidate", "manual_review"):
            if entry not in ref_cache:
                ref_cache[entry] = (_grep_count(entry, ".py"), _grep_count(entry, ".md"))
            py_ct, md_ct = ref_cache[entry]
            total = py_ct + md_ct
            refs.append({
                "candidate_path": rel, "referenced_by": f"py:{py_ct};md:{md_ct}",
                "reference_type": "code+docs" if py_ct else ("docs_only" if md_ct else "none"),
                "reference_count": total,
                "status": "manual_review" if total > 0 else ("archive_candidate" if action == "archive_candidate" else "manual_review"),
                "notes": "py refs block auto-archive" if py_ct else ("doc refs -> manual_review" if md_ct else "no refs"),
            })
            final_action = action
            if py_ct > 0 or md_ct > 0:
                final_action = "manual_review"
            plan.append({
                "old_path": rel,
                "proposed_archive_path": f"outputs/archive/server_obsolete_branch_residue/{rel}",
                "size_bytes": size, "reason": reason, "risk_level": risk,
                "referenced_by_current_branch": total > 0,
                "action": final_action if entry not in _SAFE_ARCHIVE else "archive_candidate",
                "reversible": "yes",
                "notes": f"refs py:{py_ct} md:{md_ct}",
            })

    # outputs/ subdirs
    outdir = _REPO / "outputs"
    if outdir.exists():
        for entry in sorted(os.listdir(outdir)):
            if entry in _KEEP_OUTPUTS:
                add_inv(f"outputs/{entry}", "dir", "", "", 1, "valid_deliverable", False,
                        "valid/active output", "high", "keep", "")
                continue
            p = outdir / entry
            size, files = _dir_size(p) if p.is_dir() else (p.stat().st_size, 1)
            add_inv(f"outputs/{entry}", "dir", size, files, 0, "unknown", True,
                    "untracked output of unknown validity", "medium", "manual_review", "")
            plan.append({
                "old_path": f"outputs/{entry}",
                "proposed_archive_path": f"outputs/archive/server_obsolete_branch_residue/outputs/{entry}",
                "size_bytes": size, "reason": "untracked output; unknown validity",
                "risk_level": "medium", "referenced_by_current_branch": "unknown",
                "action": "manual_review", "reversible": "yes", "notes": f"files={files}",
            })
    return inv, plan, refs


def _write_csv(path: Path, rows: list[dict], cols: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore")
        w.writeheader(); w.writerows(rows)


def apply_archive(refs: list[dict]) -> list[dict]:
    """Move ONLY the _SAFE_ARCHIVE allowlist. Never overwrite; never delete."""
    ref_by = {r["candidate_path"]: r for r in refs}
    manifest = []
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    for rel in _SAFE_ARCHIVE:
        src = _REPO / rel
        if not src.exists():
            continue
        # zero code references required
        py_refs = _grep_count(rel, ".py")
        if py_refs:
            continue  # safety: never move something referenced by code
        size, _ = _dir_size(src) if src.is_dir() else (src.stat().st_size, 1)
        dst = _ARCHIVE / rel
        if dst.exists():
            stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
            dst = _ARCHIVE / f"{rel}.{stamp}"
        dst.parent.mkdir(parents=True, exist_ok=True)
        os.replace(src, dst)
        manifest.append({
            "old_path": rel, "new_path": str(dst.relative_to(_REPO)),
            "size_bytes": size, "reason": "temp/staging obsolete residue, unreferenced by code",
            "moved_at": now, "reversible": "yes",
            "referenced_by_current_branch": ref_by.get(rel, {}).get("reference_count", 0),
            "notes": "move-only; restore by moving back",
        })
    return manifest


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="move the safe allowlist to archive")
    args = ap.parse_args()

    tracked = _tracked_set()
    untracked_top = _untracked_toplevel()
    inv, plan, refs = build_inventory(tracked, untracked_top)

    _write_csv(_OUT / "server_file_inventory.csv", inv,
               ["path", "type", "size_bytes", "mtime", "git_status", "tracked_by_current_branch",
                "ignored_by_git", "top_level_dir", "likely_category", "cleanup_candidate",
                "cleanup_reason", "risk_level", "action", "notes"])
    _write_csv(_OUT / "server_residue_reference_check.csv", refs,
               ["candidate_path", "referenced_by", "reference_type", "reference_count", "status", "notes"])
    _write_csv(_OUT / "server_cleanup_plan.csv", plan,
               ["old_path", "proposed_archive_path", "size_bytes", "reason", "risk_level",
                "referenced_by_current_branch", "action", "reversible", "notes"])

    cand = [r for r in inv if r["cleanup_candidate"]]
    arch = [r for r in plan if r["action"] == "archive_candidate"]
    mrev = [r for r in plan if r["action"] == "manual_review"]
    print(f"tracked_files={len(tracked)} inventory_rows={len(inv)} candidates={len(cand)}")
    print(f"archive_candidate={len(arch)} manual_review={len(mrev)}")

    if args.apply:
        manifest = apply_archive(refs)
        _write_csv(_ARCHIVE / "archive_manifest.csv", manifest,
                   ["old_path", "new_path", "size_bytes", "reason", "moved_at", "reversible",
                    "referenced_by_current_branch", "notes"])
        moved_bytes = sum(int(m["size_bytes"]) for m in manifest)
        print(f"APPLIED: moved {len(manifest)} items, {moved_bytes} bytes -> {_ARCHIVE}")
        for m in manifest:
            print(f"  {m['old_path']} -> {m['new_path']}")
    else:
        print("DRY-RUN: no files moved. Review outputs/cleanup_inventory/ then re-run with --apply.")


if __name__ == "__main__":
    main()
