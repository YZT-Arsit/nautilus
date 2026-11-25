"""Independent V3 research paths and freeze helpers."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import sys

ROOT=Path(__file__).resolve().parents[1]
NAUTILUS=ROOT.parents[1]
sys.path[:0]=[str(ROOT),str(NAUTILUS)]
from v2.common import setup


def inputs():
    cfg,_,v1,v2=setup()
    cfg={**cfg,**json.loads((ROOT/"configs/v3.json").read_text())}
    out=(ROOT/cfg["output"]).resolve()
    if out in (v1,v2) or v1 in out.parents or v2 in out.parents:
        raise ValueError("unsafe output path")
    out.mkdir(parents=True,exist_ok=True)
    require_space(out,cfg)
    return cfg,v1,v2,out


def require_space(path,cfg):
    if shutil.disk_usage(path).free/2**30<cfg["disk_reserve_gib"]:
        raise OSError("research stopped at disk reserve")


def digest(path):
    with Path(path).open("rb") as f:
        return hashlib.file_digest(f,"sha256").hexdigest()


def verify_prior(v1,v2):
    report={}
    for root in (v1,v2):
        manifest=root/"artifact_manifest.json"
        items=json.loads(manifest.read_text())["artifacts"]
        for x in items:
            if digest(root/x["path"])!=x["sha256"]:
                raise ValueError(f"frozen {root.name} file mismatch: {x['path']}")
        report[root.name]={"sha256":digest(manifest),"verified_files":len(items)}
    return report


def write_json(path,data):
    def encode(obj):
        if hasattr(obj,"item"):
            return obj.item()
        return str(obj)
    path=Path(path)
    temp=path.with_suffix(".tmp")
    temp.write_text(json.dumps(data,indent=2,default=encode),encoding="utf-8")
    temp.replace(path)


def utc():
    return datetime.now(timezone.utc).isoformat()
