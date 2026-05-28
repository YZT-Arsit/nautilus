# Remote access — Windows server + sync workflow

How to reach `D:\nautilus` on the production Windows host, install the runtime
dependencies, and sync code changes. No credentials are committed; the
authentication setup is described in terms of what to create, not what was
created.

## 1. SSH access

The remote uses key-based authentication. To set up a new client:

```bash
# 1. Generate a dedicated key (no passphrase only if you trust the local box)
ssh-keygen -t ed25519 -f ~/.ssh/qfe_remote_ed25519 \
    -C "qfe-remote-access-$(date +%Y%m%d)"

# 2. Append the public key to the server's administrators_authorized_keys.
#    (quant_data is an Administrator account; Windows OpenSSH ignores the
#     per-user authorized_keys for admins and reads from this file instead.)
cat ~/.ssh/qfe_remote_ed25519.pub
# Then on the server:
#   Add-Content -Path C:\ProgramData\ssh\administrators_authorized_keys -Value "<pubkey line>"
#   icacls C:\ProgramData\ssh\administrators_authorized_keys /inheritance:r \
#       /grant "Administrators:F" /grant "SYSTEM:F"
#   Restart-Service sshd

# 3. Add a local ~/.ssh/config entry
cat >> ~/.ssh/config <<'EOF'

Host nautilus-server
    HostName 172.16.112.81
    User quant_data
    IdentityFile ~/.ssh/qfe_remote_ed25519
    IdentitiesOnly yes
    PreferredAuthentications publickey
    StrictHostKeyChecking accept-new
EOF

# 4. Verify
ssh nautilus-server "hostname"
# expected: DESKTOP-4QM8PFQ
```

If banner exchange times out from a new network: the server's firewall does
connection-tracking that drops banner packets from unknown IPs. Have the
server admin whitelist your source IP.

## 2. Project-local git

The server uses **PortableGit** at `D:\nautilus\.tools\PortableGit\`. It was
installed via [scripts/install_git_portable.ps1](../../scripts/install_git_portable.ps1)
because `winget install Git.Git` is blocked by the corporate firewall
(MS package CDN unreachable) while github.com is reachable.

To re-install or upgrade:

```powershell
# Option A — script downloads the latest release itself
powershell -NoProfile -ExecutionPolicy Bypass -File D:\nautilus\scripts\install_git_portable.ps1

# Option B — pre-download the zip on a machine with github-release access and scp it across
scp MinGit-X.Y.Z-64-bit.zip nautilus-server:D:/nautilus/.tools-cache/
ssh nautilus-server "powershell -File D:\nautilus\scripts\install_git_portable.ps1 -SourceZip D:\nautilus\.tools-cache\MinGit-X.Y.Z-64-bit.zip -Force"
```

The script persists git on the **user** PATH (not system PATH), so subsequent
non-interactive SSH sessions pick it up automatically.

## 3. Git topology

| Remote | URL | Role |
|---|---|---|
| `origin` (server) | `https://github.com/nautechsystems/nautilus_trader.git` | Public upstream Nautilus repo. Used to pull upstream Nautilus changes. |
| `origin` (local) | `https://github.com/YZT-Arsit/nautilus_trader.git` | Personal fork. Where qfe development happens. |
| `fork` (server) | `https://github.com/YZT-Arsit/nautilus_trader.git` | Added on server so the personal fork is reachable from `D:\nautilus` for code review / cherry-pick. |

The server's working tree contains uncommitted modifications and untracked
local experiments. **Do not** `git checkout`, `git pull`, or `git reset`
against the server's working tree without first stashing or backing it up —
you will silently destroy in-progress work.

## 4. Sync workflow (current — interim)

Until the server's working tree is cleaned, the deployment path for
`quant_feature_engine` and the helper scripts under `scripts/` is:

```bash
# 1. Commit and push from local
cd ~/path/to/local/nautilus
git add quant_feature_engine scripts/validate_qfe_*.py scripts/scan_*.py \
    scripts/install_git_portable.ps1 internal_examples/build_qfe_*.py
git commit -m "..."
git push origin develop

# 2. Make the new tip visible on the server (no checkout, no merge)
ssh nautilus-server "powershell -Command 'cd D:\nautilus; git fetch fork develop'"

# 3. For now, also scp the actual files (until the working tree is clean
#    enough to git-checkout safely):
scp -i ~/.ssh/qfe_remote_ed25519 -o IdentitiesOnly=yes -r \
    quant_feature_engine nautilus-server:D:/nautilus/
scp -i ~/.ssh/qfe_remote_ed25519 -o IdentitiesOnly=yes \
    scripts/{validate_qfe_*.py,scan_*.py,install_git_portable.ps1} \
    nautilus-server:D:/nautilus/scripts/
scp -i ~/.ssh/qfe_remote_ed25519 -o IdentitiesOnly=yes \
    internal_examples/build_qfe_raw_from_catalog.py \
    nautilus-server:D:/nautilus/internal_examples/
```

This is double-write (git + scp) and is **deliberately temporary**. See §5.

## 5. Sync workflow (target — once working tree is clean)

Once the server's modified-tracked + untracked working tree is reconciled,
switch to the audit-and-checkout pattern:

```powershell
# On the server
cd D:\nautilus
git fetch fork develop
# Paths that belong to qfe — replace only those, leave everything else alone
git checkout fork/develop -- quant_feature_engine
git checkout fork/develop -- scripts/validate_qfe_mvp.py
git checkout fork/develop -- scripts/validate_qfe_real_data.py
git checkout fork/develop -- scripts/scan_cffex_catalog.py
git checkout fork/develop -- scripts/install_git_portable.ps1
git checkout fork/develop -- internal_examples/build_qfe_raw_from_catalog.py
```

`git checkout <commit> -- <path>` updates only the specified paths and stages
them, leaving every other tracked / untracked file in the working tree
untouched. This is safer than `git pull` for a server in a known dirty state.

A future helper [scripts/sync_qfe_from_fork.ps1](../../scripts/) (not yet
written) should encapsulate this list so it stays in lockstep with the qfe
file inventory.

## 6. Verifying local↔remote parity

After any sync, verify both sides have the same content:

```bash
# Hash the qfe tree on both ends and diff. Any non-empty output means drift.
diff \
    <(cd quant_feature_engine && find . -type f -name "*.py" -o -name "*.yaml" -o -name "*.md" -o -name "*.txt" | sort | xargs shasum) \
    <(ssh nautilus-server "cd D:/nautilus/quant_feature_engine; & 'D:/nautilus/.tools/PortableGit/usr/bin/find.exe' . -type f \( -name '*.py' -o -name '*.yaml' -o -name '*.md' -o -name '*.txt' \) | sort | xargs 'D:/nautilus/.tools/PortableGit/usr/bin/sha1sum.exe'")
```

A future [scripts/verify_remote_parity.ps1](../../scripts/) (not yet written)
should encapsulate this and exit non-zero on any drift — suitable for CI or
a pre-deploy gate.

## 7. Dependency install on the server

The project venv is at `D:\nautilus\.venv` and already has the qfe runtime
deps. To re-install from a lockfile (once one exists per backlog item A1):

```powershell
cd D:\nautilus
.\.venv\Scripts\python.exe -m pip install -r quant_feature_engine\requirements.txt
```

A future [scripts/install_qfe.ps1](../../scripts/) (backlog item A4) will wrap
this with version pin + venv refresh.

## 8. Running the framework on the server

All harnesses are documented in [VALIDATION_REPORT.md §6](VALIDATION_REPORT.md).
For convenience, the most common command:

```powershell
ssh nautilus-server "powershell -NoProfile -Command 'cd D:\nautilus; \
    .\.venv\Scripts\python.exe -m pytest quant_feature_engine\tests -q'"
```

## 9. What is **not** to be committed

- SSH private keys (`~/.ssh/qfe_remote_ed25519`, server-side host keys).
- Any file containing the server password (constraint #4 of the engagement).
- Raw or derived market data (`D:\nautilus\data\raw\`, `D:\nautilus\outputs\`
  except for the synthetic inventory CSV).
- The downloaded `MinGit-*.zip` (it's binary, ~40MB, and not source).
