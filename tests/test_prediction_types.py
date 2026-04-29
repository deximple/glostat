from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from glostat.predictor.types import (
    Prediction,
    PredictionIn,
    SignalContribution,
    SignalContributionIn,
    default_disclaimer,
    prediction_sha256,
    prediction_to_canonical_json,
)

# v1.0 — Prediction / SignalContribution dataclass + Pydantic boundary tests.


def _make_signal(
    *,
    name: str = "PEAD",
    value: float | None = 0.18,
    direction: str = "up",
    auc: float = 0.586,
    sharpe: float = 0.629,
    n: int = 298,
    skip_reason: str | None = None,
    sources: tuple[str, ...] = ("snap1", "snap2"),
) -> SignalContribution:
    return SignalContribution(
        name=name, value=value, direction=direction,  # type: ignore[arg-type]
        calibration_auc=auc, calibration_sharpe=sharpe,
        n_samples=n,
        skip_reason=skip_reason,
        source_snapshot_ids=sources,
    )


def _make_prediction(**kwargs) -> Prediction:
    defaults = {
        "ticker": "AAPL",
        "horizon": "swing_30d",
        "issued_at": datetime(2026, 4, 29, 12, 0, tzinfo=UTC),
        "up_probability": 0.532,
        "down_probability": 0.318,
        "sideways_probability": 0.150,
        "expected_return_bps": 24.0,
        "confidence_interval_bps": (-56.0, 104.0),
        "base_rate_up": 0.50,
        "edge_over_baseline_pp": 3.2,
        "contributing_signals": (_make_signal(),),
        "next_triggers": ("Next earnings → PEAD",),
        "evidence_hash": "0" * 64,
        "prompt_versions": (("PEAD", "abc"),),
        "disclaimer": default_disclaimer(),
        "calibration_period": (date(2024, 1, 1), date(2026, 3, 31)),
        "git_commit": "abc123def4567",
        "market": "XNAS",
    }
    defaults.update(kwargs)
    return Prediction(**defaults)  # type: ignore[arg-type]


def test_prediction_constructs_when_probs_sum_to_one() -> None:
    p = _make_prediction()
    assert abs(p.up_probability + p.down_probability + p.sideways_probability - 1.0) < 1e-6


def test_prediction_rejects_probability_drift() -> None:
    with pytest.raises(ValueError, match="probabilities sum to"):
        _make_prediction(
            up_probability=0.5, down_probability=0.4, sideways_probability=0.2,
        )


def test_prediction_rejects_negative_prob() -> None:
    with pytest.raises(ValueError):
        _make_prediction(up_probability=-0.1, down_probability=0.7, sideways_probability=0.4)


def test_prediction_rejects_prob_above_one() -> None:
    with pytest.raises(ValueError):
        _make_prediction(up_probability=1.1, down_probability=-0.05, sideways_probability=-0.05)


def test_prediction_rejects_inverted_ci() -> None:
    with pytest.raises(ValueError, match="confidence_interval_bps low="):
        _make_prediction(confidence_interval_bps=(50.0, 10.0))


def test_prediction_requires_evidence_hash() -> None:
    with pytest.raises(ValueError, match="evidence_hash"):
        _make_prediction(evidence_hash="")


def test_prediction_requires_at_least_one_contribution() -> None:
    with pytest.raises(ValueError, match="contributing_signals"):
        _make_prediction(contributing_signals=())


def test_prediction_requires_disclaimer() -> None:
    with pytest.raises(ValueError, match="disclaimer"):
        _make_prediction(disclaimer="")


def test_prediction_active_signal_count_excludes_skipped() -> None:
    sigs = (
        _make_signal(name="A", direction="up"),
        _make_signal(name="B", value=None, direction="skip", sources=()),
    )
    p = _make_prediction(contributing_signals=sigs)
    assert p.active_signal_count == 1
    assert p.total_signal_count == 2


def test_signal_contribution_skip_requires_value_none() -> None:
    with pytest.raises(ValueError, match="skip direction requires value=None"):
        SignalContribution(
            name="X", value=0.5, direction="skip",
            calibration_auc=0.5, calibration_sharpe=0.0, n_samples=0,
        )


def test_signal_contribution_non_skip_disallows_skip_reason() -> None:
    with pytest.raises(ValueError, match="non-skip direction with skip_reason"):
        SignalContribution(
            name="X", value=0.5, direction="up",
            calibration_auc=0.5, calibration_sharpe=0.0, n_samples=10,
            skip_reason="should not have a reason",
        )


def test_signal_contribution_rejects_auc_out_of_range() -> None:
    with pytest.raises(ValueError, match="calibration_auc"):
        SignalContribution(
            name="X", value=0.5, direction="up",
            calibration_auc=1.5, calibration_sharpe=0.0, n_samples=10,
        )


def test_signal_contribution_rejects_negative_n_samples() -> None:
    with pytest.raises(ValueError, match="n_samples"):
        SignalContribution(
            name="X", value=0.5, direction="up",
            calibration_auc=0.5, calibration_sharpe=0.0, n_samples=-1,
        )


def test_canonical_json_roundtrips() -> None:
    p = _make_prediction()
    j = prediction_to_canonical_json(p)
    assert "up_probability" in j
    assert "evidence_hash" in j
    assert "personal use" in j.lower() or "Personal use" in j


def test_prediction_sha256_is_deterministic() -> None:
    p1 = _make_prediction()
    p2 = _make_prediction()
    assert prediction_sha256(p1) == prediction_sha256(p2)


def test_prediction_sha256_changes_on_probability_change() -> None:
    p1 = _make_prediction()
    p2 = _make_prediction(
        up_probability=0.40, down_probability=0.40, sideways_probability=0.20
    )
    assert prediction_sha256(p1) != prediction_sha256(p2)


def test_pydantic_signal_contribution_in_to_dataclass() -> None:
    s_in = SignalContributionIn(
        name="PEAD", value=0.5, direction="up",
        calibration_auc=0.586, calibration_sharpe=0.629, n_samples=298,
    )
    sc = s_in.to_dataclass()
    assert sc.name == "PEAD"
    assert sc.value == 0.5
    assert sc.direction == "up"


def test_pydantic_prediction_in_to_dataclass() -> None:
    p_in = PredictionIn(
        ticker="aapl",
        horizon="swing_30d",
        issued_at=datetime(2026, 4, 29, 12, 0, tzinfo=UTC),
        up_probability=0.532,
        down_probability=0.318,
        sideways_probability=0.150,
        expected_return_bps=24.0,
        confidence_interval_bps=(-56.0, 104.0),
        base_rate_up=0.5,
        edge_over_baseline_pp=3.2,
        contributing_signals=[
            SignalContributionIn(
                name="PEAD", value=0.5, direction="up",
                calibration_auc=0.586, calibration_sharpe=0.629, n_samples=298,
            ),
        ],
        next_triggers=["Next earnings"],
        evidence_hash="0" * 64,
        disclaimer=default_disclaimer(),
        calibration_period_start=date(2024, 1, 1),
        calibration_period_end=date(2026, 3, 31),
        git_commit="abc1234",
    )
    pred = p_in.to_dataclass()
    assert pred.ticker == "AAPL"  # uppercase normalization
    assert pred.horizon == "swing_30d"
    assert pred.contributing_signals[0].name == "PEAD"


def test_default_disclaimer_mentions_not_advice() -> None:
    text = default_disclaimer()
    assert "not investment advice" in text.lower() or "Not investment advice" in text
