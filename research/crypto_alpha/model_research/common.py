"""Independent Phase 3 paths and byte-for-byte custody."""
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT),str(ROOT.parents[1])]
from factor_research.main import freeze
from v3.common import digest, require_space


def settings():
    phase = json.loads((ROOT/"configs/model_research_v1.json").read_text())
    base = (ROOT/phase["foundation"]).resolve()
    factor = (ROOT/phase["factor_research"]).resolve()
    out = (ROOT/phase["output"]).resolve()
    if out.parent!=base.parent or out.name!="model_research_v1" or factor==out:
        raise ValueError("use independent model research output")
    out.mkdir(parents=True,exist_ok=True)
    cfg = {**json.loads((base/"resolved_config.json").read_text()),**phase}
    require_space(out,cfg)
    return cfg,base,factor,out


def custody(base,factor):
    f=freeze(base,"c83d1ba6cbc4d7e0fb696c8e2a8f36324a62c66c")
    path=factor/"artifact_manifest.json"
    rows=json.loads(path.read_text())["artifacts"]
    for r in rows:
        if digest(factor/r["path"])!=r["sha256"]:
            raise ValueError(f"Factor Research changed: {r['path']}")
    source=json.loads((factor/"selected_factor_sets.json").read_text())["sets"]
    names={"FULL_88":source["full"],"NO_DERIVATIVES_83":source["without_Derivatives"],"STABLE_24":source["stable"]}
    assert [len(set(x)) for x in names.values()]==[88,83,24]
    assert set(names["NO_DERIVATIVES_83"])<=set(names["FULL_88"]) and set(names["STABLE_24"])<=set(names["FULL_88"])
    return names,dict(foundation=f,factor_research=dict(manifest_sha256=digest(path),artifacts=len(rows),
            selected_factor_sets_sha256=digest(factor/"selected_factor_sets.json")))
