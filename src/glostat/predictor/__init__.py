from __future__ import annotations

from glostat.predictor.calibration import (
    CalibrationTable,
    ThesisCalibration,
    is_active,
    load_calibration,
)
from glostat.predictor.composite import predict
from glostat.predictor.types import (
    Prediction,
    PredictionIn,
    SignalContribution,
    prediction_to_canonical_json,
)

# v1.0 — predictor package. Reframes GLOSTAT from "decision engine" (BUY/SELL action,
# 8/8 Sharpe-gate FAIL) to "prediction tool" (probability + evidence, calibration as
# data). Lives alongside the legacy verdict_builder which is now deprecated.
# TITAN parallel: TITAN outputs Verdict for the user to decide. We do the same
# globally + open-source, with explicit non-advice disclaimer per Prediction.

__all__ = [
    "CalibrationTable",
    "Prediction",
    "PredictionIn",
    "SignalContribution",
    "ThesisCalibration",
    "is_active",
    "load_calibration",
    "predict",
    "prediction_to_canonical_json",
]
