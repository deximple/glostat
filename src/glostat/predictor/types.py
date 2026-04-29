from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

# Prediction v1.0 — the predictor reframe artifact.
# Replaces Verdict (action: BUY/HOLD/SELL) with Prediction (probability + evidence).
# Compliance posture: information tool, NOT investment advice.
# Calibration data lives on each SignalContribution so the user can see WHY a signal
# pushed the probability one way (its measured AUC, Sharpe, sample size).

Horizon = Literal["intraday", "swing_5d", "swing_30d", "long_3y"]
Direction = Literal["up", "down", "neutral", "skip"]

_DEFAULT_DISCLAIMER: str = (
    "Personal use only. Not investment advice. "
    "Calibration data is historical and does not guarantee future performance. "
    "Redistribution, broadcast, or syndication is prohibited (INV-GS-024)."
)

_PROB_TOL: float = 1e-6


@dataclass(frozen=True, slots=True)
class SignalContribution:
    name: str                                    # e.g. "PEAD", "Sector_Momentum"
    value: float | None                          # raw signal score (None if skipped)
    direction: Direction                         # "up" | "down" | "neutral" | "skip"
    calibration_auc: float                       # measured AUC from hindcast
    calibration_sharpe: float                    # measured Sharpe from hindcast
    n_samples: int                               # hindcast sample size
    skip_reason: str | None = None               # e.g. "ticker not in KOSPI200"
    source_snapshot_ids: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        if self.direction == "skip" and self.value is not None:
            raise ValueError(
                f"SignalContribution {self.name}: skip direction requires value=None"
            )
        if self.direction != "skip" and self.skip_reason is not None:
            raise ValueError(
                f"SignalContribution {self.name}: non-skip direction with skip_reason"
            )
        if not 0.0 <= self.calibration_auc <= 1.0:
            raise ValueError(
                f"SignalContribution {self.name}: calibration_auc out of [0,1]"
            )
        if self.n_samples < 0:
            raise ValueError(
                f"SignalContribution {self.name}: n_samples negative"
            )


@dataclass(frozen=True, slots=True)
class Prediction:
    ticker: str
    horizon: Horizon
    issued_at: datetime
    up_probability: float                        # [0, 1]
    down_probability: float
    sideways_probability: float
    expected_return_bps: float
    confidence_interval_bps: tuple[float, float] # (low, high), 1-sigma
    base_rate_up: float                          # universe/horizon historical baseline
    edge_over_baseline_pp: float                 # percentage points above baseline
    contributing_signals: tuple[SignalContribution, ...]
    next_triggers: tuple[str, ...]
    evidence_hash: str
    prompt_versions: tuple[tuple[str, str], ...] # ((thesis, sha256), ...)
    disclaimer: str
    calibration_period: tuple[date, date]
    git_commit: str
    market: str = "XNAS"

    def __post_init__(self) -> None:
        for prob_name in ("up_probability", "down_probability", "sideways_probability"):
            v = getattr(self, prob_name)
            if not 0.0 <= v <= 1.0:
                raise ValueError(f"{prob_name} {v} out of [0, 1]")
        total = self.up_probability + self.down_probability + self.sideways_probability
        if abs(total - 1.0) > _PROB_TOL:
            raise ValueError(
                f"probabilities sum to {total:.6f}, expected 1.0"
            )
        if not 0.0 <= self.base_rate_up <= 1.0:
            raise ValueError(f"base_rate_up {self.base_rate_up} out of [0, 1]")
        low, high = self.confidence_interval_bps
        if low > high:
            raise ValueError(
                f"confidence_interval_bps low={low} > high={high}"
            )
        if not self.evidence_hash:
            raise ValueError("evidence_hash empty")
        if not self.contributing_signals:
            raise ValueError("contributing_signals must contain at least one entry")
        if not self.disclaimer:
            raise ValueError("disclaimer required for compliance posture")

    @property
    def active_signal_count(self) -> int:
        return sum(1 for s in self.contributing_signals if s.direction != "skip")

    @property
    def total_signal_count(self) -> int:
        return len(self.contributing_signals)


# Pydantic boundary validator — used at CLI/JSON serialization edges only.

class SignalContributionIn(BaseModel):
    model_config = ConfigDict(frozen=True, str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=64)
    value: float | None = None
    direction: Direction
    calibration_auc: float = Field(ge=0.0, le=1.0)
    calibration_sharpe: float
    n_samples: int = Field(ge=0)
    skip_reason: str | None = None
    source_snapshot_ids: list[str] = Field(default_factory=list)

    def to_dataclass(self) -> SignalContribution:
        return SignalContribution(
            name=self.name,
            value=self.value,
            direction=self.direction,
            calibration_auc=self.calibration_auc,
            calibration_sharpe=self.calibration_sharpe,
            n_samples=self.n_samples,
            skip_reason=self.skip_reason,
            source_snapshot_ids=tuple(self.source_snapshot_ids),
        )


class PredictionIn(BaseModel):
    model_config = ConfigDict(frozen=True, str_strip_whitespace=True)

    ticker: str = Field(min_length=1, max_length=12)
    horizon: Horizon
    issued_at: datetime
    up_probability: float = Field(ge=0.0, le=1.0)
    down_probability: float = Field(ge=0.0, le=1.0)
    sideways_probability: float = Field(ge=0.0, le=1.0)
    expected_return_bps: float
    confidence_interval_bps: tuple[float, float]
    base_rate_up: float = Field(ge=0.0, le=1.0)
    edge_over_baseline_pp: float
    contributing_signals: list[SignalContributionIn] = Field(min_length=1)
    next_triggers: list[str] = Field(default_factory=list)
    evidence_hash: str = Field(min_length=64, max_length=64)
    prompt_versions: dict[str, str] = Field(default_factory=dict)
    disclaimer: str = Field(min_length=1)
    calibration_period_start: date
    calibration_period_end: date
    git_commit: str = Field(min_length=1)
    market: str = "XNAS"

    @field_validator("ticker")
    @classmethod
    def _ticker_uppercase(cls, v: str) -> str:
        return v.upper()

    def to_dataclass(self) -> Prediction:
        return Prediction(
            ticker=self.ticker,
            horizon=self.horizon,
            issued_at=self.issued_at,
            up_probability=self.up_probability,
            down_probability=self.down_probability,
            sideways_probability=self.sideways_probability,
            expected_return_bps=self.expected_return_bps,
            confidence_interval_bps=self.confidence_interval_bps,
            base_rate_up=self.base_rate_up,
            edge_over_baseline_pp=self.edge_over_baseline_pp,
            contributing_signals=tuple(
                s.to_dataclass() for s in self.contributing_signals
            ),
            next_triggers=tuple(self.next_triggers),
            evidence_hash=self.evidence_hash,
            prompt_versions=tuple(sorted(self.prompt_versions.items())),
            disclaimer=self.disclaimer,
            calibration_period=(self.calibration_period_start, self.calibration_period_end),
            git_commit=self.git_commit,
            market=self.market,
        )


def prediction_to_canonical_json(p: Prediction) -> str:
    payload = asdict(p)
    payload["issued_at"] = p.issued_at.isoformat()
    payload["calibration_period"] = [
        p.calibration_period[0].isoformat(),
        p.calibration_period[1].isoformat(),
    ]
    payload["confidence_interval_bps"] = list(p.confidence_interval_bps)
    payload["next_triggers"] = list(p.next_triggers)
    payload["prompt_versions"] = list(p.prompt_versions)
    payload["contributing_signals"] = [
        {
            **asdict(s),
            "source_snapshot_ids": list(s.source_snapshot_ids),
        }
        for s in p.contributing_signals
    ]
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)


def prediction_sha256(p: Prediction) -> str:
    return hashlib.sha256(prediction_to_canonical_json(p).encode("utf-8")).hexdigest()


def default_disclaimer() -> str:
    return _DEFAULT_DISCLAIMER


__all__ = [
    "Direction",
    "Horizon",
    "Prediction",
    "PredictionIn",
    "SignalContribution",
    "SignalContributionIn",
    "default_disclaimer",
    "prediction_sha256",
    "prediction_to_canonical_json",
]
