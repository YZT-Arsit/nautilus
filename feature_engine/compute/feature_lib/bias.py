"""Price bias (乖离率) relative to a completed-bar simple moving average."""
from __future__ import annotations

from collections import deque
from typing import Any

from feature_engine.compute.feature_lib.base import _AbstractFeature, _bar_field, _ts_ns, FeatureUpdate, WarmupRequirement
from feature_engine.compute.spec import FeatureSpec


class BiasFeature(_AbstractFeature):
    def __init__(self, spec: FeatureSpec) -> None:
        super().__init__(spec); self._window=int(spec.window or 20); self._values=deque(maxlen=self._window); self._sum=0.0; self._latest=None
        if self._window <= 0: raise ValueError("BIAS window must be positive")

    def warmup_required(self)->WarmupRequirement:return WarmupRequirement(self._window,unit="bars")
    @property
    def is_ready(self)->bool:return self._latest is not None
    def reset(self)->None:self._values.clear();self._sum=0.0;self._latest=None;self._reset_base()
    def update(self,event:Any)->FeatureUpdate:
        self._event_count+=1; ts=_ts_ns(event,self._spec.trigger.time_semantics); close=_bar_field(event,self._spec.input_field or "close")
        if close is None:return self._no_change()
        if len(self._values)==self._window:self._sum-=self._values[0]
        self._values.append(close);self._sum+=close
        if len(self._values)==self._window:
            mean=self._sum/self._window;self._latest=(close/mean-1.0)*100.0 if mean else None
        return self._emit(self._latest,self.is_ready,True,source_event_time_ns=ts)
    def state_dict(self)->dict:return {**self._base_state(),"values":list(self._values),"sum":self._sum,"latest":self._latest}
    def load_state_dict(self,state:dict)->None:self._load_base(state);self._values=deque(state.get("values",[]),maxlen=self._window);self._sum=float(state.get("sum",0.0));self._latest=state.get("latest")
