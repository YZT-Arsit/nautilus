# Remote access and deployment workflow

How to reach `D:\nautilus` on the production Windows host, keep code in sync,
and install the runtime dependencies.  No credentials are stored in this file.

---

## 1. SSH access

Authentication is key-based.  To set up a new client machine:

```bash
# Generate a dedicated key pair (no passphrase only if you trust the client box)
ssh-keygen -t ed25519 -f ~/.ssh/qfe_remote_ed25519 \
    -C "qfe-remote-access-$(date +%Y%m%d)"

# Print the public key, then install it on the server (see note below)
cat ~/.ssh/qfe_remote_ed25519.pub
```

The `quant_data` user is an **Administrator** on the Windows host.  Windows
OpenSSH ignores the per-user `authorized_keys` for admins and reads from the
system-wide file instead.  On the server run:

```powershell
Add-Content `
    -Path C:\ProgramData\ssh\administrators_authorized_keys `
    -Value "<paste public key line here>"

# Lock down ACLs (sshd rejects files readable by non-admins)
icacls C:\ProgramData\ssh\administrators_authorized_keys `
    /inheritance:r /grant "Administrators:F" /grant "SYSTEM:F"

Restart-Service sshd
```

Add a local `~/.ssh/config` entry so you don't repeat flags every command:

```
Host nautilus-server
    HostName 172.16.112.81
    User quant_data
    IdentityFile ~/.ssh/qfe_remote_ed25519
    IdentitiesOnly yes
    PreferredAuthentications publickey
    StrictHostKeyChecking accept-new
```

Verify:

```bash
ssh nautilus-server "hostname"
# expected: DESKTOP-4QM8PFQ
```

If banner exchange times out from a new network location, the server firewall
is blocking the client IP.  Have the server admin whitelist it; the issue is
not with the key.

---

## 2. Git topology

| Remote alias | URL | Role |
|---|---|---|
| `origin` on local machine | `https://github.com/YZT-Arsit/nautilus_trader.git` | Personal fork.  This is where all qfe development is committed and pushed. |
| `origin` on server (`D:\nautilus`) | `https://github.com/nautechsystems/nautilus_trader.git` | Public upstream Nautilus.  Used for upstream merges only; never push qfe work here. |
| `fork` on server | `https://github.com/YZT-Arsit/nautilus_trader.git` | Added so the server can fetch the personal fork without changing the meaning of `origin`. |

The working branch on both sides is `develop`.

---

## 3. Recommended sync workflow (current — git-based)

This is the standard deployment path.  Use it for all qfe changes.

**On the local machine after making changes:**

```bash
# Stage and commit
git add <changed paths>
git commit -m "qfe: <concise description>"

# Push to personal fork
git push origin develop
```

**On the server to receive the changes:**

```bash
ssh nautilus-server "powershell -NoProfile -Command \
  'cd D:\nautilus; git fetch fork develop; git merge --ff-only fork/develop'"
```

The `--ff-only` flag guarantees the server never creates a merge commit.  If it
fails with "not a fast-forward", the server has local commits that haven't been
incorporated into the fork — investigate before merging.

A shorter form that combines fetch + fast-forward pull:

```bash
ssh nautilus-server "powershell -NoProfile -Command \
  'cd D:\nautilus; git pull --ff-only fork develop'"
```

**Verify the sync landed:**

```bash
# Both commands must print the same commit hash
git rev-parse HEAD
ssh nautilus-server "powershell -NoProfile -Command 'cd D:\nautilus; git rev-parse HEAD'"
```

---

## 4. Fallback: scp-based file transfer (legacy — avoid unless git is broken)

Use scp only when the git workflow is unavailable, for example during the
initial bootstrap before git is installed.  It does not update the branch ref
and produces invisible local/remote drift.

```bash
# Copy the whole qfe package directory
scp -i ~/.ssh/qfe_remote_ed25519 -o IdentitiesOnly=yes -r \
    quant_feature_engine nautilus-server:D:/nautilus/

# Copy individual helper scripts
scp -i ~/.ssh/qfe_remote_ed25519 -o IdentitiesOnly=yes \
    scripts/validate_qfe_mvp.py \
    scripts/validate_qfe_real_data.py \
    scripts/scan_cffex_catalog.py \
    nautilus-server:D:/nautilus/scripts/

# Copy bridge script
scp -i ~/.ssh/qfe_remote_ed25519 -o IdentitiesOnly=yes \
    internal_examples/build_qfe_raw_from_catalog.py \
    nautilus-server:D:/nautilus/internal_examples/
```

After any scp transfer, reconcile by running git diff on both sides to confirm
what landed.

---

## 5. Git on the Windows server (PortableGit)

`git` is installed project-locally at `D:\nautilus\.tools\PortableGit\` via
[scripts/install_git_portable.ps1](../../scripts/install_git_portable.ps1).
This is a project-local install of Git-for-Windows MinGit; it does not require
administrator rights and does not modify the system.

`winget install Git.Git` was unavailable because the Microsoft package CDN is
blocked at the server's firewall; GitHub release assets are reachable.

To re-install or upgrade on the server:

```powershell
# Option A — script fetches the latest release from GitHub itself
powershell -NoProfile -ExecutionPolicy Bypass `
    -File D:\nautilus\scripts\install_git_portable.ps1

# Option B — pre-download the zip on a machine with full internet access, scp across
scp MinGit-X.Y.Z-64-bit.zip nautilus-server:D:/nautilus/.tools-cache/
ssh nautilus-server "powershell -File D:\nautilus\scripts\install_git_portable.ps1 `
    -SourceZip D:\nautilus\.tools-cache\MinGit-X.Y.Z-64-bit.zip -Force"
```

**Gitignored directories on the server (not committed, not deleted):**

| Path | Contents |
|---|---|
| `.tools/` | The extracted PortableGit binary tree. |
| `.tools-cache/` | Downloaded MinGit `.zip` installer and pre-cleanup forensic snapshots. |

Both paths are in `.gitignore` (lines 88–89) so they do not appear in
`git status` output.  They are safe to delete and re-create at any time using
the install script above.

---

## 6. Dependency install on the server

```powershell
# One-command install from the pinned lockfile; verifies imports; runs pytest
powershell -NoProfile -ExecutionPolicy Bypass `
    -File D:\nautilus\scripts\install_qfe.ps1

# Skip the test run (faster, e.g. in CI pre-steps)
powershell -NoProfile -ExecutionPolicy Bypass `
    -File D:\nautilus\scripts\install_qfe.ps1 -SkipTests
```

The script resolves the interpreter in order: `-PythonExe` flag →
`$env:QFE_PYTHON` → `D:\nautilus\.venv\Scripts\python.exe`.  It installs from
`quant_feature_engine/requirements.lock.txt` (exact pins) and falls back to
`requirements.txt` with a warning if the lockfile is absent.

---

## 7. Running the test suite

```bash
# From local
ssh nautilus-server "powershell -NoProfile -Command \
  'cd D:\nautilus; .\.venv\Scripts\python.exe -m pytest quant_feature_engine\tests -q'"
```

All harness commands are documented with their exact output in
[VALIDATION_REPORT.md §6](VALIDATION_REPORT.md#6-reproduction--full-command-sequence).

---

## 8. What must not be committed

- SSH private keys (`~/.ssh/qfe_remote_ed25519` and any server-side host keys).
- The server password (not stored anywhere in the project — never add it).
- Raw or derived market data under `D:\nautilus\data\raw\` or `outputs/`
  (except the tracked `outputs/qfe_catalog_inventory/cffex_inventory.csv`).
- The MinGit installer zip (binary, ~40 MB, not source).
- `.tools/` and `.tools-cache/` (already gitignored).

---

## 9. Recovering the upstream Nautilus tip

Before the server's `develop` was aligned to the personal fork, it was at
upstream Nautilus commit `93078e3` (Kraken/Polymarket fixes, May 2026).  That
commit is preserved as a local branch on the server:

```bash
ssh nautilus-server "powershell -NoProfile -Command \
  'cd D:\nautilus; git log backup/upstream-develop-before-reset --oneline -5'"
```

To cherry-pick specific upstream changes into the fork, use standard git
cherry-pick from that ref.
