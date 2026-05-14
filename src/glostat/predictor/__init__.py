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
from glostat.predictor.types import (
    Prediction,
    PredictionIn,
    SignalContribution,
    SignalContributionIn,
    prediction_sha256,
    prediction_to_canonical_json,
)

# v2.0 — predictor package. v1.0 reframed GLOSTAT from "decision engine" to
# "prediction tool" (probability + evidence, calibration as data). v1.4 added
# the 5-component confidence_v2 model that modulates Brier ensemble weights
# (INV-GS-112). v2.0 removes the sizing-tier attachment (INV-GS-111
# deprecated) to keep the predictor strictly probability-out.

__all__ = [
    "CalibrationTable",
    "ConfidenceV2",
    "Prediction",
    "PredictionIn",
    "SignalContribution",
    "SignalContributionIn",
    "ThesisCalibration",
    "compute_confidence_v2",
    "confidence_v2_from_calibration",
    "is_active",
    "load_calibration",
    "predict",
    "prediction_sha256",
    "prediction_to_canonical_json",
]
