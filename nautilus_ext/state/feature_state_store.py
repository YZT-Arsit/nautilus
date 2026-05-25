from __future__ import annotations

import json
from pathlib import Path
import re


class JsonFeatureStateStore:
    """Persist pipeline checkpoint metadata, not native indicator internals."""

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

    def _path_for(self, key: str) -> Path:
        safe_key = re.sub(r"[^A-Za-z0-9_.-]+", "_", key.strip())
        safe_key = safe_key.strip("._")
        if not safe_key:
            raise ValueError("state key must contain at least one safe character.")
        return self.root_dir / f"{safe_key}.json"
