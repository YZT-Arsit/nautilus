"""DAG resolution: topo order, cycle detection, level grouping."""
from __future__ import annotations

import pytest

from feature_engine.core.dag import FeatureDAG


def test_simple_dag_topo_order() -> None:
    """vwm_zscore_60 depends on vwm_20 → vwm_20 must come first."""
    dag = FeatureDAG(["vwm_zscore_60"])
    assert dag.order.index("vwm_20") < dag.order.index("vwm_zscore_60")


def test_dag_levels_are_disjoint() -> None:
    dag = FeatureDAG(["vwm_zscore_60", "sma_20", "rsi_14"])
    seen: set[str] = set()
    for level in dag.levels:
        assert not (set(level) & seen)
        seen.update(level)


def test_dag_pulls_transitive_deps() -> None:
    """Asking only for the derived feature still instantiates its dep."""
    dag = FeatureDAG(["vwm_zscore_60"])
    assert "vwm_20" in dag.order


def test_unknown_feature_raises() -> None:
    with pytest.raises(KeyError):
        FeatureDAG(["nonexistent_feature_xyz"])
