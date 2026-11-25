"""Read and validate a frozen signal export intended for an offline consumer."""
import numpy as np
import pandas as pd

EXPORT_COLUMNS=["prediction_time","execution_time","model_version","symbol",
                "prediction_score","relative_strength","target_weight"]


def validate_export(frame, delay_minutes):
    required=frame[EXPORT_COLUMNS].copy()
    for col in ("prediction_time","execution_time"):
        if not isinstance(required[col].dtype,pd.DatetimeTZDtype) or str(required[col].dt.tz)!="UTC":
            raise ValueError(f"{col} must be a UTC timestamp")
        if required[col].isna().any():
            raise ValueError(f"missing {col}")
    if len(required)==0 or required.model_version.nunique()!=1 or required.model_version.isna().any():
        raise ValueError("one frozen model version is required")
    if required.duplicated(["prediction_time","symbol"]).any() or required.symbol.isna().any():
        raise ValueError("one target per symbol and prediction time is required")
    if not (required.execution_time-required.prediction_time).eq(pd.Timedelta(minutes=delay_minutes)).all():
        raise ValueError("execution delay does not match the experiment contract")
    if not np.isfinite(required.target_weight).all():
        raise ValueError("targets must be finite")
    if (required.prediction_score.isna() & required.target_weight.ne(0)).any():
        raise ValueError("an unscored symbol cannot have nonzero target")
    grouped=required.groupby("prediction_time").target_weight
    if not np.allclose(grouped.sum(),0,atol=1e-12):
        raise ValueError("targets must be dollar neutral")
    return required.sort_values(["execution_time","symbol"]).reset_index(drop=True)
