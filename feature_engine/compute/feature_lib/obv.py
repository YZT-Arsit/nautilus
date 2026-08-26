"""Canonical On-Balance Volume and its rolling mean."""
from __future__ import annotations

from collections import deque
from typing import Any

from feature_engine.compute.feature_lib.base import _AbstractFeature, _bar_field, _ts_ns, FeatureUpdate, WarmupRequirement
from feature_engine.compute.spec import FeatureSpec


class OnBalanceVolumeFeature(_AbstractFeature):
    def __init__(self,spec:FeatureSpec)->None:
        super().__init__(spec);self._window=int(spec.window or 20);self._output=str(spec.params.get("output","obv"));self._previous_close=None;self._obv=0.0;self._history=deque(maxlen=self._window);self._latest=None
        if self._window<=0 or self._output not in {"obv","sma"}:raise ValueError("OBV requires positive window and obv/sma output")
    def warmup_required(self)->WarmupRequirement:return WarmupRequirement(2 if self._output=="obv" else self._window,unit="bars")
    @property
    def is_ready(self)->bool:return self._latest is not None
    def reset(self)->None:self._previous_close=None;self._obv=0.0;self._history.clear();self._latest=None;self._reset_base()
    def update(self,event:Any)->FeatureUpdate:
        self._event_count+=1;ts=_ts_ns(event,self._spec.trigger.time_semantics);close=_bar_field(event,"close");volume=_bar_field(event,"volume")
        if close is None or volume is None:return self._missing_field("close/volume")
        if self._previous_close is not None:self._obv += volume if close>self._previous_close else -volume if close<self._previous_close else 0.0
        self._previous_close=close;self._history.append(self._obv)
        self._latest=self._obv if self._output=="obv" and self._event_count>=2 else sum(self._history)/self._window if len(self._history)==self._window else None
        return self._emit(self._latest,self.is_ready,True,source_event_time_ns=ts)
    def state_dict(self)->dict:return {**self._base_state(),"previous_close":self._previous_close,"obv":self._obv,"history":list(self._history),"latest":self._latest}
    def load_state_dict(self,state:dict)->None:self._load_base(state);self._previous_close=state.get("previous_close");self._obv=float(state.get("obv",0.0));self._history=deque(state.get("history",[]),maxlen=self._window);self._latest=state.get("latest")
