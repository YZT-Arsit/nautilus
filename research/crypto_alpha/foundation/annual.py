"""Frozen validation/refit with a single estimator and metric contract."""
from time import perf_counter

import numpy as np
import pandas as pd

from foundation.model import Model, OBJECTIVES


def subset(view, columns):
    return {**view, "x":view["x"][:, :, columns]}


def train_family(family, params, cfg, panel, window, features, path):
    cols = [panel.factors.index(name) for name in features]
    fit = subset(panel.view(window.train_start, window.validation_start), cols)
    val = subset(panel.view(window.validation_start, window.test_start), cols)
    full = subset(panel.view(window.train_start, window.test_start), cols)
    test = subset(panel.view(window.test_start, window.test_end, mature=False), cols)
    started = perf_counter()
    candidate = Model(family, params, cfg, features).fit(fit, val)
    validation_score = candidate.predict(val)
    m, curve = candidate.evaluate(validation_score, val)
    annual = Model(family, params, cfg, features).fit(full, rounds=candidate.rounds)
    mask = np.isfinite(full["labels"][cfg["target"]])
    assert (full["available"][mask] <= window.test_start.value).all()
    annual.save(path)
    loaded = Model.load(path, cfg)
    predictions = loaded.predict(test)
    np.testing.assert_allclose(predictions, annual.predict(test), rtol=1e-7, atol=1e-8, equal_nan=True)
    o, ts = loaded.evaluate(predictions, test)
    clock = pd.Timestamp(full["available"][mask].max(), unit="ns", tz="UTC")
    statistics = dict(model=family, objective=OBJECTIVES[family], features=len(features), target=cfg["target"],
                      selected_rounds=candidate.rounds, fit_years=cfg["fit_years"], refit_samples=int(mask.sum()),
                      refit_sections=int(mask.any(axis=1).sum()), latest_mature_label=clock,
                      elapsed_seconds=perf_counter()-started, **vars(window))
    pd.DataFrame(candidate.history, columns=["round","loss","validation_raw_ic"]).assign(
        stage="validation").to_csv(path/"validation_history.csv", index=False)
    pd.DataFrame(annual.history, columns=["round","loss","validation_raw_ic"]).assign(
        stage="refit").to_csv(path/"refit_history.csv", index=False)
    return (dict(**statistics, split="validation", **m), dict(**statistics, split="historical_test", **o),
            curve, ts, test, predictions)
