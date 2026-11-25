"""Persisted estimator contract over the existing CPU model engines."""
from pathlib import Path

import joblib
import lightgbm
import numpy as np
import torch

from foundation.evaluation import evaluate
from models.cpu import Preprocessor, fit_model, net, prediction
from v3.common import write_json
import json

OBJECTIVES = {"Ridge": "MSE", "LightGBM": "MSE", "MLP-IC": "time-section Pearson IC loss"}


class Model:
    def __init__(self, family, params, cfg, features):
        if family not in OBJECTIVES:
            raise ValueError(f"unknown standard family {family}")
        self.family, self.params, self.cfg, self.features = family, params, cfg, list(features)

    def fit(self, train, validation=None, rounds=None):
        label = self.cfg["target"]
        outcome = train["labels"][label]
        valid = np.isfinite(outcome)
        if not valid.any():
            raise ValueError("no mature informative training labels")
        usable = np.isfinite(train["x"][valid]).any(axis=0)
        if not usable.all():
            raise ValueError(f"selected factors without any finite training values: {np.array(self.features)[~usable]}")
        self.processor = Preprocessor.fit(train["x"][valid])
        val = None if validation is None else (
            self.processor.transform(validation["x"]), validation["raw"], validation["eligible"])
        self.estimator, self.rounds, self.history = fit_model(
            self.family, self.params, self.processor.transform(train["x"]), outcome, val, self.cfg, rounds)
        return self

    def predict(self, view):
        predict = prediction(self.estimator, self.family, self.processor.transform(view["x"]))
        return np.where(view["eligible"], predict, np.nan)

    def evaluate(self, scores, view):
        return evaluate(scores, view, self.cfg["min_assets"], self.cfg["target"])

    def save(self, path):
        path = Path(path)
        path.mkdir(parents=True, exist_ok=True)
        model = path/"weights"
        if self.family == "LightGBM":
            self.estimator.save_model(str(model))
        elif self.family == "MLP-IC":
            torch.save(self.estimator.state_dict(), model)
        else:
            joblib.dump(self.estimator, model)
        joblib.dump(self.processor, path/"preprocessing.joblib")
        write_json(path/"model.json", dict(family=self.family, parameters=self.params,
                   target=self.cfg["target"], features=self.features, rounds=self.rounds))

    @classmethod
    def load(cls, path, cfg):
        path = Path(path)
        metadata = json.loads((path/"model.json").read_text(encoding="utf-8"))
        model = cls(metadata["family"], metadata["parameters"], {**cfg, "target":metadata["target"]},
                    metadata["features"])
        model.processor = joblib.load(path/"preprocessing.joblib")
        model.rounds = metadata["rounds"]
        if model.family == "LightGBM":
            model.estimator = lightgbm.Booster(model_file=str(path/"weights"))
        elif model.family == "MLP-IC":
            model.estimator = net(len(model.features))
            model.estimator.load_state_dict(torch.load(path/"weights", weights_only=True, map_location="cpu"))
        else:
            model.estimator = joblib.load(path/"weights")
        return model
