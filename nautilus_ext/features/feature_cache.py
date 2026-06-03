"""
FeatureQueryCache — simple LRU cache for OfflineFeatureStore query results.

Avoids re-reading Parquet files when the same (instrument, feature_set, range)
is queried multiple times — e.g. across strategy iterations or multiple passes
through the same historical window.

This is adapted from the DataHander ``DataPartitionCache`` concept but
simplified: no transaction locking, no Ray, no Windows paths.
"""
from __future__ import annotations

import logging
from collections import OrderedDict
from typing import Any

import pandas as pd

log = logging.getLogger(__name__)

_DEFAULT_MAX_ENTRIES = 64


class FeatureQueryCache:
    """LRU cache for OfflineFeatureStore.query() results.

    Parameters
    ----------
    max_entries : int
        Maximum number of distinct query result DataFrames to keep in memory.
        Least-recently-used entries are evicted when the limit is reached.
    """

    def __init__(self, max_entries: int = _DEFAULT_MAX_ENTRIES) -> None:
        self._max = max_entries
        self._cache: OrderedDict[str, pd.DataFrame] = OrderedDict()

    def _key(self, **kwargs: Any) -> str:
        return str(tuple(sorted((k, repr(v)) for k, v in kwargs.items())))

    def get(self, **kwargs: Any) -> pd.DataFrame | None:
        """Return cached result, or None on miss.  Moves entry to MRU position."""
        key = self._key(**kwargs)
        if key not in self._cache:
            return None
        self._cache.move_to_end(key)
        return self._cache[key]

    def put(self, df: pd.DataFrame, **kwargs: Any) -> None:
        """Cache a query result.  Evicts LRU if at capacity."""
        key = self._key(**kwargs)
        if key in self._cache:
            self._cache.move_to_end(key)
        else:
            if len(self._cache) >= self._max:
                evicted = next(iter(self._cache))
                del self._cache[evicted]
                log.debug("FeatureQueryCache: evicted key %r", evicted)
            self._cache[key] = df

    def invalidate(self, **kwargs: Any) -> bool:
        """Remove one entry by its query kwargs.  Returns True if it was present."""
        key = self._key(**kwargs)
        if key in self._cache:
            del self._cache[key]
            return True
        return False

    def clear(self) -> None:
        """Remove all cached entries."""
        self._cache.clear()

    def __len__(self) -> int:
        return len(self._cache)
