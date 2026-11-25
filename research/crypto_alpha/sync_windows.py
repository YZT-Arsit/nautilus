"""Push tracked module files over Tailscale and compare hashes with Windows."""
import argparse
import base64
import hashlib
import json
from pathlib import Path, PurePosixPath
import subprocess
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parent


def sync(config):
    key = str(Path(config["identity_file"]).expanduser())
    remote = config["host"]
    dest = config["remote_module"]
    if not dest.replace("\\","/").endswith("/research/crypto_alpha"):
        raise ValueError("expected existing Nautilus research module destination")
    ssh = ["ssh","-i",key,"-o","BatchMode=yes","-o","ConnectTimeout=10",remote]
    files = subprocess.check_output(["git","ls-files","-z"],cwd=ROOT).decode().split("\0")
    actual = {f:hashlib.sha256((ROOT/f).read_bytes()).hexdigest() for f in files if f and (ROOT/f).is_file()}
    old = subprocess.check_output(["git","rev-parse","HEAD"],cwd=ROOT,text=True).strip()
    with tempfile.TemporaryDirectory() as folder:
        package = Path(folder)/"crypto_code.zip"
        with zipfile.ZipFile(package,"w",zipfile.ZIP_DEFLATED) as archive:
            for f in actual:
                if ".." in PurePosixPath(f).parts:
                    raise ValueError(f)
                archive.write(ROOT/f,f)
            archive.writestr("code_manifest.json",json.dumps({"git_commit":old,"files":actual},indent=2))
        package_hash = hashlib.sha256(package.read_bytes()).hexdigest()
        remote_zip = f"crypto_code_{package_hash[:16]}.zip"
        subprocess.run([*ssh,f'if not exist "{dest}" mkdir "{dest}"'],check=True)
        subprocess.run(["scp","-q","-i",key,str(package),f"{remote}:{dest}/{remote_zip}"],check=True)
        body = f"""
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
Set-Location '{dest}'
if ((Get-FileHash '{remote_zip}' -Algorithm SHA256).Hash.ToLower() -ne '{package_hash}') {{ throw 'Archive hash mismatch' }}
Expand-Archive -LiteralPath '{remote_zip}' -DestinationPath . -Force
$manifest = Get-Content -Raw code_manifest.json | ConvertFrom-Json
foreach ($file in $manifest.files.PSObject.Properties) {{
  if ((Get-FileHash -LiteralPath $file.Name -Algorithm SHA256).Hash.ToLower() -ne $file.Value) {{ throw ('Checksum mismatch ' + $file.Name) }}
}}
Remove-Item -LiteralPath '{remote_zip}'
Write-Output ('SYNC_VERIFIED ' + $manifest.git_commit + ' ' + @($manifest.files.PSObject.Properties).Count)
"""
        encoded = base64.b64encode(body.encode("utf-16-le")).decode("ascii")
        subprocess.run([*ssh,"powershell -NoProfile -NonInteractive -EncodedCommand",encoded],check=True)


if __name__=="__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--config",default=ROOT/"configs/windows-sync.example.json")
    args=parser.parse_args()
    sync(json.loads(Path(args.config).read_text()))
