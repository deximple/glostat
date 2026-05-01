from __future__ import annotations

from glostat.predictor.calibration import (
    CalibrationTable,
    ThesisCalibration,
    is_active,
    load_calibration,
)
from glostat.predictor.composite import predict
from glostat.predictor.confidence_v2 import (
    ConfidenceV2,
    compute_confidence_v2,
    confidence_v2_from_calibration,
)
from glostat.predictor.dca_sizing import (
    SizingRecommendation,
    build_sizing_recommendation,
    compute_w_value,
    w_to_sizing_recommendation,
)
from glostat.predictor.types import (
    Prediction,
    PredictionIn,
    SignalContribution,
    prediction_to_canonical_json,
)

# v1.4 — predictor package. v1.0 reframed GLOSTAT from "decision engine" to
# "prediction tool" (probability + evidence, calibration as data). v1.4 adds
# (N3) DCA sizing recommendation as INFORMATION (calibration-derived sizing
# tier; INV-GS-101 preserved) and (N4) 5-component confidence_v2 model that
# modulates Brier ensemble weights (INV-GS-112).

__all__ = [
    "CalibrationTable",
    "ConfidenceV2",
    "Prediction",
    "PredictionIn",
    "SignalContribution",
    "SizingRecommendation",
    "ThesisCalibration",
    "build_sizing_recommendation",
    "compute_confidence_v2",
    "compute_w_value",
    "confidence_v2_from_calibration",
    "is_active",
    "load_calibration",
    "predict",
    "prediction_to_canonical_json",
    "w_to_sizing_recommendation",
]
