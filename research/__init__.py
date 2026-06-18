"""Research layer: offline dataset/label/split tooling for ML signal research.

This package is **separate** from ``feature_engine`` on purpose: it does batch,
full-history feature/label construction for model training, whereas
``feature_engine`` is a point-in-time online transform. Nothing here imports
``nautilus_trader``; the heavy numeric core is pure-Python (stdlib ``math``) so
it runs without numpy/pandas, with pandas/pyarrow used only optionally at the
I/O boundary of the dataset builder.

Phase A exposes:

* :mod:`research.features`       - point-in-time ML V1 feature functions
* :mod:`research.label_builder`  - forward-return + cost-aware 3-class labels
* :mod:`research.splits`         - train/val/test assignment + horizon purge
* :mod:`research.dataset_builder`- assemble features+labels+splits into a dataset
"""
