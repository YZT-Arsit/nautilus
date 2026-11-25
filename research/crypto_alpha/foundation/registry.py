"""Versioned built-ins plus a callable registration interface for causal factors."""
from dataclasses import dataclass, field
import json
from typing import Callable

import numpy as np
import pandas as pd

from factors.library import add_cross_section, compute
from v2.features import cross, local_features


@dataclass(frozen=True)
class Factor:
    factor_name: str
    formula: str
    input_fields: str
    lookback: str
    calculate: Callable
    parameters: dict = field(default_factory=dict)
    stage: str = "symbol"
    availability_time: str = "completed inputs at prediction"
    upstream_reference: str = "Crypto research original"


class Registry:
    def __init__(self):
        self._extra = {"symbol": {}, "section": {}}

    def register(self, factor: Factor):
        if factor.stage not in self._extra:
            raise ValueError(f"unknown factor stage {factor.stage}")
        if any(factor.factor_name in stage for stage in self._extra.values()):
            raise ValueError(f"duplicate factor {factor.factor_name}")
        self._extra[factor.stage][factor.factor_name] = factor

    def _apply(self, frame, rows, stage, context):
        for name, spec in self._extra[stage].items():
            if name in frame:
                raise ValueError(f"factor overrides built-in {name}")
            if stage == "section":
                fields = ["prediction_time", "symbol", "eligible", "feature_available_time",
                          *[r["factor_name"] for r in rows]]
                context = frame[fields]
            value = spec.calculate(context, **spec.parameters)
            if not isinstance(value, pd.Series):
                raise TypeError(f"factor {name} must return an indexed pandas Series")
            frame[name] = value.reindex(frame.index).replace([np.inf, -np.inf], np.nan)
            rows.append(dict(factor_name=name, formula=spec.formula, lookback=spec.lookback,
                             input_fields=spec.input_fields, source="registered extension",
                             availability_time=spec.availability_time, category=stage,
                             implementation_version="foundation", upstream_reference=spec.upstream_reference,
                             implementation_path=f"{spec.calculate.__module__}.{spec.calculate.__qualname__}",
                             parameters=json.dumps(spec.parameters, sort_keys=True),
                             original_formula_numeric_check="NOT_CLAIMED"))

    def symbol(self, data, clock, funding):
        if not funding:
            raise ValueError("standard nine-asset fixture requires existing settlement history for funding factors")
        base, rows = compute(data, clock, funding)
        ext, other = local_features(data, clock, funding)
        frame = pd.concat([base, ext], axis=1)
        for r in rows:
            r["implementation_path"] = "factors/library.py::compute"
        for r in other:
            r["implementation_path"] = "v2/features.py::local_features"
        self._apply(frame, rows, "symbol", data)
        return frame, rows + other

    def section(self, panel, rows):
        panel.loc[~panel.eligible, [r["factor_name"] for r in rows]] = np.nan
        add_cross_section(panel, rows)
        for r in rows:
            r.setdefault("implementation_path", "factors/library.py::add_cross_section")
        ext = cross(panel)
        for r in ext:
            r["implementation_path"] = "v2/features.py::cross"
        rows.extend(ext)
        self._apply(panel, rows, "section", None)
        for r in rows:
            r.setdefault("parameters", json.dumps({"window": r["lookback"],
                                                    "fixed_coefficients_and_operators": r["formula"]}))
            r.setdefault("upstream_reference", "Crypto research original")
            r.setdefault("original_formula_numeric_check", "NOT_AN_UPSTREAM_FORMULA")
        return pd.DataFrame(rows)
