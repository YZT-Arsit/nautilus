"""Per-time Pearson IC, Spearman IC and sample ICIR."""
import numpy as np
from scipy.stats import rankdata


def section_ic(x, y, min_assets, rank=False):
    mask = np.isfinite(x) & np.isfinite(y)
    count = mask.sum(axis=-1)
    if rank:
        x = rankdata(np.where(mask, x, np.nan), axis=-1, nan_policy="omit")
        y = rankdata(np.where(mask, y, np.nan), axis=-1, nan_policy="omit")
    denominator = np.maximum(count, 1)[..., None]
    x = np.where(mask, x, 0.0)
    y = np.where(mask, y, 0.0)
    x = np.where(mask, x - x.sum(axis=-1, keepdims=True) / denominator, 0)
    y = np.where(mask, y - y.sum(axis=-1, keepdims=True) / denominator, 0)
    norm = np.sqrt((x*x).sum(axis=-1) * (y*y).sum(axis=-1))
    good = (count >= min_assets) & (norm > 0)
    return np.divide((x*y).sum(axis=-1), norm, out=np.full(norm.shape, np.nan), where=good), count


def score_summary(values):
    valid = values[np.isfinite(values)]
    mean = float(valid.mean()) if valid.size else np.nan
    std = float(valid.std(ddof=1)) if valid.size > 1 else np.nan
    return {"mean": mean, "std": std, "ir": mean/std if std > 0 else np.nan,
            "positive_ratio": float((valid > 0).mean()) if valid.size else np.nan,
            "valid_sections": len(valid), "skipped_sections": len(values) - len(valid)}
