"""Distributed runner using Ray (optional dependency).

Why Ray as the default distributed backend
------------------------------------------
* Python-native; no JVM round-trip means Arrow stays zero-copy across actors.
* Actor model maps cleanly onto "one partition → one task → one Polars frame".
* Trivially scales from 1 → N machines without rewriting code.
* Plays well with the rest of the quant ML stack (RLlib, Tune, Train).

Dask / Spark / DataFusion-Ballista are equally legitimate; swapping is a matter
of replacing this file. The :func:`run` signature is intentionally identical to
:meth:`BatchEngine.run` so the offline CLI flips backends with a flag.
"""
from __future__ import annotations

import logging
from pathlib import Path

from feature_engine.execution.batch_engine import (
    BackfillSpec,
    _run_partition,
)
from feature_engine.storage.metadata import Manifest

logger = logging.getLogger(__name__)


class RayBatchEngine:
    """Same contract as :class:`BatchEngine` but fans out via Ray."""

    def __init__(
        self,
        *,
        raw_root: Path | str,
        feature_root: Path | str,
        manifest: Manifest,
        ray_address: str | None = None,
        num_cpus_per_task: float = 1.0,
    ) -> None:
        try:
            import ray  # noqa: PLC0415
        except ImportError as e:  # pragma: no cover
            raise RuntimeError("Ray is not installed; pip install 'ray[default]'") from e

        if not ray.is_initialized():
            ray.init(address=ray_address, ignore_reinit_error=True)

        self._ray = ray
        self.raw_root = Path(raw_root)
        self.feature_root = Path(feature_root)
        self.manifest = manifest
        self.num_cpus_per_task = num_cpus_per_task

        # Wrap the worker once; Ray captures the function by reference.
        self._remote_runner = ray.remote(num_cpus=num_cpus_per_task)(_run_partition)

    def run(
        self,
        partitions: list[dict[str, str]],
        feature_names: list[str],
        *,
        force: bool = False,  # noqa: ARG002 — manifest dedup happens upstream
    ) -> list[dict]:
        specs = [
            BackfillSpec(
                raw_filters=p,
                feature_names=feature_names,
                raw_store_root=self.raw_root,
                feature_store_root=self.feature_root,
            )
            for p in partitions
        ]
        refs = [self._remote_runner.remote(s) for s in specs]
        results = self._ray.get(refs)

        all_manifest_rows: list[dict] = []
        for r in results:
            all_manifest_rows.extend(r.get("manifest_rows", []))
        self.manifest.append(all_manifest_rows)
        return results
