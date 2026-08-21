"""Deterministic target resolution for composable strategy modules."""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum


class ModulePriority(IntEnum):
    SIZING = 10
    EXPOSURE_CAP = 20
    ADD_POSITION = 30
    REDUCE_POSITION = 40
    EXIT = 50
    SESSION_FLATTEN = 60


@dataclass(frozen=True)
class ModuleProposal:
    module_id: str
    target_exposure: float
    priority: ModulePriority
    reason: str

    def __post_init__(self) -> None:
        if not -1 <= self.target_exposure <= 1:
            raise ValueError("target_exposure must be in [-1, 1]")


def resolve_module_proposals(
    *,
    base_target: float,
    current_exposure: float,
    proposals: list[ModuleProposal],
) -> ModuleProposal:
    """
    Resolve one timestamp without allowing risk-increasing overrides.

    Flatten wins.  Otherwise the highest-priority risk-reducing proposal wins;
    add-position requests are considered only when no reduction/exit exists.
    Exposure caps always bound the final absolute target.
    """
    if not -1 <= base_target <= 1 or not -1 <= current_exposure <= 1:
        raise ValueError("exposures must be in [-1, 1]")
    if not proposals:
        return ModuleProposal("alpha", base_target, ModulePriority.SIZING, "base_target")
    flatten = [
        p
        for p in proposals
        if abs(p.target_exposure) <= 1e-12 and p.priority >= ModulePriority.EXIT
    ]
    if flatten:
        return max(flatten, key=lambda p: (p.priority, p.module_id))
    reductions = [
        p
        for p in proposals
        if p.priority >= ModulePriority.REDUCE_POSITION
        and abs(p.target_exposure) < abs(current_exposure) - 1e-12
    ]
    if reductions:
        return min(
            reductions, key=lambda p: (abs(p.target_exposure), -int(p.priority), p.module_id)
        )
    caps = [p for p in proposals if p.priority == ModulePriority.EXPOSURE_CAP]
    cap = min((abs(p.target_exposure) for p in caps), default=1.0)
    candidates = [base_target] + [
        p.target_exposure for p in proposals if p.priority <= ModulePriority.ADD_POSITION
    ]
    target = max(candidates, key=abs)
    target = max(-cap, min(cap, target))
    return ModuleProposal("module_pipeline", target, ModulePriority.EXPOSURE_CAP, "resolved_target")
