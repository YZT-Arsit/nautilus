"""Build and execute V2 research in its independent Windows task."""
import json
import os
import shutil
from time import perf_counter
import warnings

from v2.common import ROOT, config_digest, resolved_config, setup
import pandas as pd
import torch
from threadpoolctl import threadpool_limits

from v2.decay import analyze
from v2.features import prepare
from v2.reconcile import freeze
from v2.train import pipeline


def run():
    start=perf_counter()
    cfg,dates,v1,out=setup()
    warnings.filterwarnings("ignore",message="Mean of empty slice")
    warnings.filterwarnings("ignore",message="invalid value encountered in expm1")
    free=shutil.disk_usage(out).free/2**30
    if free<cfg["free_space_gib"]:
        raise RuntimeError("Windows research disk reserve too low")
    if os.name=="nt":
        from ctypes import WinDLL,wintypes
        kernel=WinDLL("kernel32")
        kernel.GetCurrentProcess.restype=wintypes.HANDLE
        kernel.SetPriorityClass.argtypes=(wintypes.HANDLE,wintypes.DWORD)
        if not kernel.SetPriorityClass(kernel.GetCurrentProcess(),16384):
            raise OSError("Cannot set below-normal research priority")
    if cfg["device"].startswith("cuda") and not torch.cuda.is_available():
        raise RuntimeError("CUDA configured but unavailable; use the CPU config")
    torch.set_num_threads(cfg["threads"])
    torch.set_num_interop_threads(1)
    cached=out/"derived/research_panel.parquet"
    recorded=out/"derived/prepared.json"
    token=config_digest()
    manifest=ROOT/"code_manifest.json"
    startup_commit=json.loads(manifest.read_text())["git_commit"] if manifest.is_file() else None
    with threadpool_limits(limits=cfg["threads"]):
        if cached.exists() and recorded.exists() and json.loads(recorded.read_text())["config_sha256"]==token:
            frame=pd.read_parquet(cached)
            names=pd.read_csv(out/"factor_registry.csv").factor_name.tolist()
        else:
            frame,names=prepare(cfg,dates,v1,out)
        print("IC DECAY",flush=True)
        analyze(frame,names,cfg,dates,v1,out)
        print("MODEL STUDY",flush=True)
        selection=pipeline(frame,names,cfg,dates,v1,out)
    frozen=json.loads((out/"v1_frozen_input.json").read_text())
    if freeze(v1,out)!=(frozen["manifest_sha256"],frozen["verified_files"]):
        raise ValueError("V1 frozen custody check failed")
    provenance={"device":cfg["device"],"cuda_available":torch.cuda.is_available(),"torch":torch.__version__,
                "peak_vram_bytes":torch.cuda.max_memory_allocated() if cfg["device"].startswith("cuda") else 0,
                "threads":cfg["threads"],"wall_seconds":perf_counter()-start,"v1_manifest_sha256":frozen["manifest_sha256"],
                "free_disk_gib":shutil.disk_usage(out).free/2**30,"production_orders":0,"demo_orders":0,
                "config_sha256":token}
    if startup_commit is not None:
        provenance["git_commit"]=startup_commit
    (out/"run_environment.json").write_text(json.dumps(provenance,indent=2))
    (out/"v2_config.json").write_text(json.dumps(resolved_config(),indent=2))
    shutil.copy2(ROOT/"v2/source_reproduction.csv",out/"source_reproduction.csv")
    print("V2_MODELS_COMPLETE",flush=True)


if __name__=="__main__":
    run()
