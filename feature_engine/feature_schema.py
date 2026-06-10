"""
FeatureSchema — formal schema definitions for feature sets.

Every feature set that writes to OfflineFeatureStore must have a registered
FeatureSetSpec.  This ensures:

1. Feature columns are documented, typed, and versioned.
2. Training and inference modules can discover columns without reading data.
3. signals.csv cannot accumulate undocumented columns silently.
4. Schema changes produce a version bump, not a silent data corruption.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class FeatureFieldSpec:
    """Specification for one output feature column."""

    name: str
    dtype: str
    nullable: bool = True
    description: str = ""


@dataclass
class FeatureSetSpec:
    """Specification for a named, versioned feature set.

    Parameters
    ----------
    feature_set_id : str
        Stable identifier, e.g. ``"vwm_features_v1"``.  Must be unique in the
        registry.  Changing semantics without changing this ID is a violation.
    version : str
        Schema version, e.g. ``"1"``.  Bump when output columns change.
    input_types : list[str]
        MarketEvent ``event_type`` values this engine accepts, e.g. ``["bar"]``.
    output_features : list[FeatureFieldSpec]
        Ordered list of output columns, each with name, dtype, nullability.
    required_history : int
        Minimum events required before the engine can emit its first non-None
        output (e.g. EMA period).
    frequency : str | None
        Bar frequency, e.g. ``"1m"``.  None = tick-level.
    instruments : list[str] | None
        None means engine applies to any instrument.
    timeframes : list[str] | None
        None means engine applies to any timeframe.
    point_in_time_safe : bool
        True when the feature uses only information available at ts_event —
        i.e. no look-ahead bias.  Must be True for all streaming engines.
        May be False only for retrospective label-engineering features.
    description : str
    owner : str
    """

    feature_set_id: str
    version: str
    input_types: list[str]
    output_features: list[FeatureFieldSpec]
    required_history: int = 0
    frequency: str | None = None
    instruments: list[str] | None = None
    timeframes: list[str] | None = None
    point_in_time_safe: bool = True
    description: str = ""
    owner: str = ""

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def output_feature_names(self) -> list[str]:
        """Return ordered list of feature column names."""
        return [f.name for f in self.output_features]

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def to_dict(self) -> dict:
        return asdict(self)

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent)

    def save(self, path: str | Path) -> Path:
        dest = Path(path)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(self.to_json(), encoding="utf-8")
        return dest

    @classmethod
    def from_dict(cls, d: dict) -> "FeatureSetSpec":
        d = dict(d)
        d["output_features"] = [
            FeatureFieldSpec(**f) if isinstance(f, dict) else f
            for f in d.get("output_features", [])
        ]
        return cls(**d)

    @classmethod
    def load(cls, path: str | Path) -> "FeatureSetSpec":
        return cls.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))
