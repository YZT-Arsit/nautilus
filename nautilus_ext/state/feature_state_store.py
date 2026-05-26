from __future__ import annotations

from typing import Protocol
import json
from pathlib import Path
import re


class FeatureStateStore(Protocol):
    def save(self, key: str, state: dict):
        ...

    def load(self, key: str) -> dict | None:
        ...

    def exists(self, key: str) -> bool:
        ...

    def delete(self, key: str) -> None:
        ...


class JsonFeatureStateStore:
    """Filesystem checkpoint backend for local or standalone deployments."""

    def __init__(self, root_dir: str = "outputs/feature_states") -> None:
        self.root_dir = Path(root_dir)

    def save(self, key: str, state: dict) -> Path:
        path = self._path_for(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(state, indent=2, ensure_ascii=True), encoding="utf-8")
        return path

    def load(self, key: str) -> dict | None:
        path = self._path_for(key)
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    def exists(self, key: str) -> bool:
        return self._path_for(key).exists()

    def delete(self, key: str) -> None:
        path = self._path_for(key)
        if path.exists():
            path.unlink()

    def _path_for(self, key: str) -> Path:
        safe_key = _safe_key(key)
        return self.root_dir / f"{safe_key}.json"


class RedisFeatureStateStore:
    """Optional JSON checkpoint backend for Redis-compatible servers, including Valkey."""

    def __init__(
        self,
        redis_url: str = "redis://localhost:6379/0",
        key_prefix: str | None = None,
        ttl_seconds: int | None = None,
    ) -> None:
        if ttl_seconds is not None and ttl_seconds < 1:
            raise ValueError("ttl_seconds must be >= 1 when provided.")
        try:
            import redis
        except ImportError as exc:
            raise ImportError(
                "Redis backend requires `redis` package. Install with pip install redis."
            ) from exc
        self._client = redis.Redis.from_url(redis_url, decode_responses=True)
        self.key_prefix = key_prefix
        self.ttl_seconds = ttl_seconds

    def save(self, key: str, state: dict) -> str:
        redis_key = self._key_for(key)
        value = json.dumps(state, ensure_ascii=True)
        self._client.set(redis_key, value, ex=self.ttl_seconds)
        return redis_key

    def load(self, key: str) -> dict | None:
        value = self._client.get(self._key_for(key))
        return None if value is None else json.loads(value)

    def exists(self, key: str) -> bool:
        return bool(self._client.exists(self._key_for(key)))

    def delete(self, key: str) -> None:
        self._client.delete(self._key_for(key))

    def _key_for(self, key: str) -> str:
        safe_key = _safe_key(key, allow_colon=True)
        return f"{self.key_prefix}:{safe_key}" if self.key_prefix else safe_key


def build_feature_state_store(
    backend: str,
    json_root_dir: str | None = None,
    redis_url: str | None = None,
    key_prefix: str | None = None,
    ttl_seconds: int | None = None,
) -> FeatureStateStore:
    normalized = backend.lower().strip()
    if normalized == "json":
        return JsonFeatureStateStore(json_root_dir or "outputs/feature_states")
    if normalized == "redis":
        return RedisFeatureStateStore(
            redis_url=redis_url or "redis://localhost:6379/0",
            key_prefix=key_prefix,
            ttl_seconds=ttl_seconds,
        )
    raise ValueError("Unknown state backend. Expected 'json' or 'redis'.")


def _safe_key(key: str, allow_colon: bool = False) -> str:
    allowed = r"[^A-Za-z0-9_.:-]+" if allow_colon else r"[^A-Za-z0-9_.-]+"
    safe_key = re.sub(allowed, "_", key.strip()).strip("._")
    if not safe_key:
        raise ValueError("state key must contain at least one safe character.")
    return safe_key
