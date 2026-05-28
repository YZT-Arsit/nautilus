"""State store abstraction + canonical key composition.

State leakage between symbols, sessions, feature versions, or different param
choices is the most common silent bug in feature engines. We fight that with
two mechanisms:

  1. **Per-symbol state inside the feature**: ``self._state[symbol]`` is the
     only shape allowed; the :class:`PerSymbolMixin` enforces this at the API
     level by tagging every row with its source symbol.

  2. **Composite checkpoint key**: when writing state to a shared store we
     compose the key from *all* the dimensions a state value depends on:
     feature name, feature version, params hash, frequency, trading_date (or
     session id), and symbol. The :func:`state_key` helper is the single
     source of truth for this composition.

Two backends are provided:
  * :class:`MemoryStateStore` – in-process dict. Default; zero deps.
  * :class:`RedisStateStore`  – Redis-backed; multi-process, durable.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


# ---------------------------------------------------------------------------
# Key composition
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class StateScope:
    """The full address of a piece of feature state.

    Every field is part of the cache key; changing any of them means we are
    looking at a *different* logical state, never an in-place update.
    """

    feature_name: str
    feature_version: int
    params_hash: str
    frequency: str
    session: str  # trading_date for offline; a session id for streaming
    symbol: str | None = None  # None → feature-level state (rare, e.g. universe stats)


def state_key(scope: StateScope) -> str:
    """Canonical string key for :class:`StateStore` lookups.

    Format::

        feature:{name}:v{version}:p{params_hash}:{frequency}:{session}[:s={symbol}]

    The first segment ``feature:`` lets a single Redis instance host multiple
    unrelated namespaces; the explicit ``v`` and ``p`` prefixes make malformed
    keys instantly visible in ``redis-cli KEYS`` output.
    """
    base = (
        f"feature:{scope.feature_name}:v{scope.feature_version}"
        f":p{scope.params_hash}:{scope.frequency}:{scope.session}"
    )
    if scope.symbol is not None:
        return f"{base}:s={scope.symbol}"
    return base


# ---------------------------------------------------------------------------
# Store backends
# ---------------------------------------------------------------------------


class StateStore(Protocol):
    """Minimal KV interface. Implementations may be sync or async-wrapped."""

    def get(self, key: str) -> bytes | None: ...
    def put(self, key: str, blob: bytes) -> None: ...
    def delete(self, key: str) -> None: ...
    def keys(self, prefix: str) -> list[str]: ...


class MemoryStateStore:
    """In-memory store. Not durable. Fast. Default for tests + single-process."""

    def __init__(self) -> None:
        self._data: dict[str, bytes] = {}

    def get(self, key: str) -> bytes | None:
        return self._data.get(key)

    def put(self, key: str, blob: bytes) -> None:
        self._data[key] = blob

    def delete(self, key: str) -> None:
        self._data.pop(key, None)

    def keys(self, prefix: str) -> list[str]:
        return [k for k in self._data if k.startswith(prefix)]


class RedisStateStore:
    """Redis-backed store for shared / durable feature state.

    Connection is lazy; instantiating the class never opens a socket. Provides
    optional namespace prefix so multiple deployments share a Redis cluster.
    """

    def __init__(
        self,
        url: str = "redis://localhost:6379/0",
        namespace: str = "qfe",
    ) -> None:
        self._url = url
        self._ns = namespace
        self._client = None  # lazy import; redis is optional

    def _conn(self):
        if self._client is None:
            import redis  # noqa: PLC0415 — optional dep

            self._client = redis.Redis.from_url(self._url)
        return self._client

    def _k(self, key: str) -> str:
        return f"{self._ns}:{key}"

    def get(self, key: str) -> bytes | None:
        return self._conn().get(self._k(key))

    def put(self, key: str, blob: bytes) -> None:
        self._conn().set(self._k(key), blob)

    def delete(self, key: str) -> None:
        self._conn().delete(self._k(key))

    def keys(self, prefix: str) -> list[str]:
        pattern = f"{self._k(prefix)}*"
        return [
            k.decode().removeprefix(f"{self._ns}:")
            for k in self._conn().scan_iter(match=pattern)
        ]
