"""Feature dependency DAG.

Each feature declares ``dependencies`` (names of upstream features). The DAG
resolves topological order and groups features into *levels* — all features in
the same level have no inter-dependency and can be evaluated in parallel.

We deliberately keep this in-memory and simple: tens-of-thousands of features
is the realistic upper bound, and the overhead of a graph library would dwarf
the work.
"""
from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass

from feature_engine.core import registry as _registry
from feature_engine.core.feature import Feature


@dataclass(frozen=True)
class _Node:
    name: str
    deps: tuple[str, ...]


class FeatureDAG:
    """Resolve and traverse a feature dependency graph.

    Parameters
    ----------
    names : The feature names the caller wants to compute. Transitive deps are
        pulled in automatically from the registry.
    """

    def __init__(self, names: list[str]) -> None:
        self._nodes: dict[str, _Node] = {}
        self._collect(names)
        self._order: list[str] = self._topo_sort()
        self._levels: list[list[str]] = self._level_partition()

    # ------------------------------------------------------------------ build

    def _collect(self, names: list[str]) -> None:
        """BFS through dependencies, pulling each required feature from the registry."""
        seen: set[str] = set()
        queue: deque[str] = deque(names)
        while queue:
            n = queue.popleft()
            if n in seen:
                continue
            seen.add(n)
            cls = _registry.get(n)
            deps = tuple(cls.meta.dependencies)
            self._nodes[n] = _Node(name=n, deps=deps)
            queue.extend(deps)

    def _topo_sort(self) -> list[str]:
        """Kahn's algorithm. Raises ``ValueError`` on cycle."""
        indegree: dict[str, int] = {n: 0 for n in self._nodes}
        adj: dict[str, list[str]] = defaultdict(list)
        for n, node in self._nodes.items():
            for d in node.deps:
                if d not in self._nodes:
                    raise ValueError(f"Feature {n!r} depends on unknown {d!r}")
                adj[d].append(n)
                indegree[n] += 1

        ready: deque[str] = deque(sorted(n for n, d in indegree.items() if d == 0))
        order: list[str] = []
        while ready:
            n = ready.popleft()
            order.append(n)
            for nxt in sorted(adj[n]):
                indegree[nxt] -= 1
                if indegree[nxt] == 0:
                    ready.append(nxt)
        if len(order) != len(self._nodes):
            remaining = set(self._nodes) - set(order)
            raise ValueError(f"Cycle detected among features: {sorted(remaining)}")
        return order

    def _level_partition(self) -> list[list[str]]:
        """Group nodes by longest path from a root → enables level-parallel exec."""
        depth: dict[str, int] = {}
        for n in self._order:
            deps = self._nodes[n].deps
            depth[n] = 1 + max((depth[d] for d in deps), default=-1)
        levels: dict[int, list[str]] = defaultdict(list)
        for n, d in depth.items():
            levels[d].append(n)
        return [sorted(levels[k]) for k in sorted(levels)]

    # ------------------------------------------------------------------ api

    @property
    def order(self) -> list[str]:
        """Linear topological order. Use for sequential execution."""
        return list(self._order)

    @property
    def levels(self) -> list[list[str]]:
        """List of independent groups; each level can run concurrently."""
        return [list(level) for level in self._levels]

    def instantiate(self) -> dict[str, Feature]:
        """Construct fresh Feature instances in topo order.

        Returns a dict keyed by name so callers can reach each instance by id.
        Engines should call this once per worker/actor (state is per-instance).
        """
        return {n: _registry.get(n)() for n in self._order}
