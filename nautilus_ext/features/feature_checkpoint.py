"""
FeatureCheckpointManager — save/load FeaturePipeline state to JSON files.

Enables warm restarts: instead of re-downloading and replaying all historical
bars on every startup, the pipeline state is checkpointed after warmup and
restored on the next run.

The checkpointing strategy mirrors VwmFeatureEngine.state_dict() — the engine
saves its full bar history so indicators can be reconstructed exactly from
the replay.  This is conservative but correct; compact indicator-native
checkpoints can be added later.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

log = logging.getLogger(__name__)


class FeatureCheckpointManager:
    """Saves and loads FeaturePipeline state to/from JSON files.

    Parameters
    ----------
    checkpoint_dir : str | Path
        Directory where checkpoint files are stored.
    """

    def __init__(self, checkpoint_dir: str | Path) -> None:
        self._dir = Path(checkpoint_dir)

    def save(self, pipeline, run_id: str) -> Path:
        """Persist pipeline state.

        Parameters
        ----------
        pipeline : FeaturePipeline
            Pipeline whose engine states will be serialised.
        run_id : str
            Identifier for this checkpoint (e.g. session timestamp).

        Returns
        -------
        Path
            Path to the written JSON file.
        """
        path = self._dir / f"{run_id}_feature_state.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        state = pipeline.state_dict()
        path.write_text(json.dumps(state, default=str, indent=2), encoding="utf-8")
        log.info("FeatureCheckpointManager: saved state → %s", path)
        return path

    def load(self, pipeline, run_id: str) -> None:
        """Restore pipeline state from a checkpoint file.

        Parameters
        ----------
        pipeline : FeaturePipeline
            Target pipeline; engines are restored in-place.
        run_id : str
            Identifier matching a previously saved checkpoint.
        """
        path = self._dir / f"{run_id}_feature_state.json"
        state = json.loads(path.read_text(encoding="utf-8"))
        pipeline.load_state_dict(state)
        log.info("FeatureCheckpointManager: restored state ← %s", path)

    def list_checkpoints(self) -> list[str]:
        """Return sorted list of available checkpoint run IDs."""
        if not self._dir.exists():
            return []
        return sorted(
            p.stem.replace("_feature_state", "")
            for p in self._dir.glob("*_feature_state.json")
        )

    def exists(self, run_id: str) -> bool:
        """Return True if a checkpoint for *run_id* exists on disk."""
        return (self._dir / f"{run_id}_feature_state.json").exists()
