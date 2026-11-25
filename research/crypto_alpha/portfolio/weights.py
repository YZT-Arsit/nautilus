"""Map causal scores to neutral targets with a per-side capped L1 allocation."""
import numpy as np
from scipy.stats import rankdata


def _allocate(magnitude, target, cap):
    out = np.zeros_like(magnitude)
    remaining = magnitude > 0
    while remaining.any() and target > 1e-14:
        allocation = magnitude[remaining] / magnitude[remaining].sum() * target
        saturated = allocation > cap
        if not saturated.any():
            out[remaining] = allocation
            break
        indices = np.flatnonzero(remaining)[saturated]
        out[indices] = cap
        remaining[indices] = False
        target -= len(indices) * cap
    return out


def weights(scores, method, gross, cap, min_assets):
    valid = np.isfinite(scores)
    strengths = np.full(scores.shape, np.nan)
    target = np.zeros_like(strengths)
    if valid.sum() < min_assets:
        return strengths, target
    values = scores[valid].astype(np.float64, copy=False)
    if method == "rank":
        values = rankdata(values)
    centered = values - (np.median(values) if method == "equal" else values.mean())
    std = centered.std(ddof=0)
    if std <= 1e-12:
        strengths[valid] = 0
        return strengths, target
    strengths[valid] = centered / std
    magnitude = np.abs(np.sign(centered) if method == "equal" else centered)
    long = np.where(centered > 0, magnitude, 0)
    short = np.where(centered < 0, magnitude, 0)
    side_budget = min(gross / 2, (long > 0).sum() * cap, (short > 0).sum() * cap)
    target[valid] = _allocate(long, side_budget, cap) - _allocate(short, side_budget, cap)
    return strengths, target
