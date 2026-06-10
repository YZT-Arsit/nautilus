"""YAML config loader.

Single dataclass per logical section so we can validate types at load time
rather than crashing deep in the engine. Uses standard library only — no
heavy schema library — because the config surface is small.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class StorageConfig:
    raw_root: str = "data/raw"
    feature_root: str = "data/features"
    manifest_root: str = "data/_meta"


@dataclass
class StreamingConfig:
    batch_ms: int = 1000
    checkpoint_every_n_batches: int = 60
    redis_url: str | None = None
    """If set, RedisStateStore is used; otherwise MemoryStateStore."""


@dataclass
class ExecutionConfig:
    backend: str = "local"  # 'local' | 'ray'
    n_workers: int | None = None
    ray_address: str | None = None


@dataclass
class FeatureSet:
    name: str
    features: list[str] = field(default_factory=list)


@dataclass
class AppConfig:
    storage: StorageConfig = field(default_factory=StorageConfig)
    streaming: StreamingConfig = field(default_factory=StreamingConfig)
    execution: ExecutionConfig = field(default_factory=ExecutionConfig)
    feature_sets: list[FeatureSet] = field(default_factory=list)


def load_config(path: Path | str) -> AppConfig:
    """Load a YAML file into :class:`AppConfig`. PyYAML is required."""
    import yaml  # noqa: PLC0415 — optional dep

    raw: dict[str, Any] = yaml.safe_load(Path(path).read_text()) or {}
    return AppConfig(
        storage=StorageConfig(**raw.get("storage", {})),
        streaming=StreamingConfig(**raw.get("streaming", {})),
        execution=ExecutionConfig(**raw.get("execution", {})),
        feature_sets=[FeatureSet(**fs) for fs in raw.get("feature_sets", [])],
    )
