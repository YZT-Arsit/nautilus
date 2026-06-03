"""
nautilus_ext.ml — machine learning interface layer.

This package provides read-only access to the Feature Data Layer for
training and inference workflows.  No ML frameworks are required or
imported by this package; the actual model (scikit-learn, PyTorch, etc.)
is plugged in by the user.

Current contents
----------------
feature_dataset.py   Load historical features from OfflineFeatureStore for training.
inference_context.py  Read latest features from OnlineFeatureStore for inference.

Future additions (not yet implemented)
---------------------------------------
model_registry.py    Registry of trained model artefacts.
feature_importance.py  Compute feature → signal correlations.
online_inference.py  Async model inference integrated with FeaturePipeline.
"""

__all__ = [
    "FeatureDatasetSpec",
    "load_feature_dataset",
    "ModelInferenceContext",
]


def __getattr__(name: str):
    if name in {"FeatureDatasetSpec", "load_feature_dataset"}:
        from nautilus_ext.ml.feature_dataset import (
            FeatureDatasetSpec,
            load_feature_dataset,
        )
        return {"FeatureDatasetSpec": FeatureDatasetSpec,
                "load_feature_dataset": load_feature_dataset}[name]
    if name == "ModelInferenceContext":
        from nautilus_ext.ml.inference_context import ModelInferenceContext
        return ModelInferenceContext
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
