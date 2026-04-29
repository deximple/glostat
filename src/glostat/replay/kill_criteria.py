from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, Final, Literal

import yaml

from glostat.core.errors import ConfigError

# E10 Contrarian / INV-GS-033: hard kill criteria with non-overridable SHUTDOWN.
# Source: docs/research/kill_criteria_design.md §4 + §8 (Phase 1 Sprint 4 thresholds).
# Decision matrix (cautious profile):
#   sharpe < 0.8 sustained 5 consecutive days  → SHUTDOWN
#   maxdd  > 15%  (immediate, single breach)   → SHUTDOWN
#   auc    < 0.62 (immediate, sprint4 gate)    → SHUTDOWN
#   oos_deg > 30% (sustained 2 cycles)         → SHUTDOWN
#   cost_passed outside [40%, 60%]             → SUSPEND_7D (band warning)
#   any single borderline → SUSPEND_7D (1 retry allowed)
#   all within threshold → CONTINUE

_KILL_CRITERIA_YAML_DEFAULT: Final[Path] = (
    Path(__file__).resolve().parents[3] / "configs" / "kill_criteria.yaml"
)

ProfileName = Literal["cautious", "balanced", "aggressive"]


class KillDecision(StrEnum):
    CONTINUE = "CONTINUE"
    SUSPEND_7D = "SUSPEND_7D"
    SHUTDOWN = "SHUTDOWN"


@dataclass(frozen=True, slots=True)
class KillThresholds:
    sharpe_min: float
    oos_degradation_max: float
    auc_min: float
    cost_passed_band: tuple[float, float]
    maxdd_max: float
    grace_period_consecutive_days: int

    @classmethod
    def from_mapping(cls, profile: Mapping[str, Any]) -> KillThresholds:
        band_raw = profile.get("cost_passed_band", [0.40, 0.60])
        band = tuple(float(x) for x in band_raw)
        if len(band) != 2:
            raise ConfigError(
                f"kill_criteria: cost_passed_band must be [low, high], got {band_raw!r}"
            )
        return cls(
            sharpe_min=float(profile["sharpe_min"]),
            oos_degradation_max=float(profile["oos_degradation_max"]),
            auc_min=float(profile["auc_min"]),
            cost_passed_band=(band[0], band[1]),
            maxdd_max=float(profile["maxdd_max"]),
            grace_period_consecutive_days=int(profile.get("grace_period_consecutive_days", 5)),
        )


@dataclass(frozen=True, slots=True)
class KillDecisionResult:
    decision: KillDecision
    profile: str
    violated_metrics: tuple[str, ...]
    borderline_metrics: tuple[str, ...]
    recommendations: tuple[str, ...]
    grace_period_remaining: int
    eligible_for_v031_pivot: bool
    evidence: tuple[tuple[str, str], ...]
    timestamp: datetime
    reason: str

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["decision"] = self.decision.value
        d["timestamp"] = self.timestamp.isoformat()
        d["evidence"] = dict(self.evidence)
        return d

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"))


@dataclass(frozen=True, slots=True)
class HindcastMetricsView:
    sharpe: float
    oos_degradation: float
    auc: float
    cost_passed_pct: float
    maxdd: float
    consecutive_violation_days: int = 0
    consecutive_oos_cycles_failed: int = 0
    compliance_clean_days: int = 90  # MVP defaults to clean (no live ops yet)


@dataclass(slots=True)
class KillCriteriaMonitor:
    profile: ProfileName = "cautious"
    config_path: Path = field(default=_KILL_CRITERIA_YAML_DEFAULT)
    thresholds: KillThresholds = field(init=False)
    pivot_thresholds: KillThresholds = field(init=False)
    pivot_compliance_days: int = field(init=False)

    def __post_init__(self) -> None:
        cfg = _load_yaml(self.config_path)
        profiles = cfg.get("profiles", {}) or {}
        if self.profile not in profiles:
            raise ConfigError(
                f"kill_criteria: profile {self.profile!r} not in {sorted(profiles)}"
            )
        self.thresholds = KillThresholds.from_mapping(profiles[self.profile])
        pivot_raw = cfg.get("v031_pivot", {}) or {}
        # v031 pivot reuses the same dataclass shape; supply maxdd default so it parses.
        pivot_full = {
            "sharpe_min": pivot_raw.get("sharpe_min", 1.2),
            "oos_degradation_max": pivot_raw.get("oos_degradation_max", 0.15),
            "auc_min": pivot_raw.get("auc_min", 0.65),
            "cost_passed_band": pivot_raw.get("cost_passed_band", [0.50, 0.65]),
            "maxdd_max": pivot_raw.get("maxdd_max", self.thresholds.maxdd_max),
            "grace_period_consecutive_days": 0,
        }
        self.pivot_thresholds = KillThresholds.from_mapping(pivot_full)
        self.pivot_compliance_days = int(pivot_raw.get("compliance_clean_days_required", 90))

    def evaluate(
        self,
        metrics: HindcastMetricsView,
        *,
        defer_shutdown: bool = False,
    ) -> KillDecisionResult:
        t = self.thresholds
        violated: list[str] = []
        borderline: list[str] = []
        recs: list[str] = []
        evidence = {
            "sharpe": f"{metrics.sharpe:.4f}",
            "oos_degradation": f"{metrics.oos_degradation:.4f}",
            "auc": f"{metrics.auc:.4f}",
            "cost_passed_pct": f"{metrics.cost_passed_pct:.4f}",
            "maxdd": f"{metrics.maxdd:.4f}",
            "consecutive_violation_days": str(metrics.consecutive_violation_days),
            "consecutive_oos_cycles_failed": str(metrics.consecutive_oos_cycles_failed),
        }

        # 1. Sharpe — sustained violation requires consecutive_violation_days ≥ grace.
        if metrics.sharpe < t.sharpe_min:
            sustained = (
                metrics.consecutive_violation_days >= t.grace_period_consecutive_days
            )
            if sustained:
                violated.append("sharpe_below_threshold_sustained")
                recs.append(
                    f"Sharpe {metrics.sharpe:.3f} < {t.sharpe_min:.2f} for "
                    f"{metrics.consecutive_violation_days} days. SHUTDOWN required."
                )
            else:
                borderline.append("sharpe_below_threshold")
                recs.append(
                    f"Sharpe {metrics.sharpe:.3f} < {t.sharpe_min:.2f} but only "
                    f"{metrics.consecutive_violation_days}/{t.grace_period_consecutive_days} "
                    "days; monitoring."
                )

        # 2. Maxdd — immediate (cumulative metric, single breach is meaningful).
        if metrics.maxdd > t.maxdd_max:
            violated.append("maxdd_exceeds_threshold")
            recs.append(
                f"Maxdd {metrics.maxdd * 100:.2f}% > {t.maxdd_max * 100:.2f}%. "
                "SHUTDOWN immediate (no grace period)."
            )

        # 3. AUC — immediate at gate (sprint4 + post-launch monthly).
        if metrics.auc < t.auc_min:
            violated.append("auc_below_threshold")
            recs.append(
                f"AUC {metrics.auc:.3f} < {t.auc_min:.2f}. "
                "Discrimination insufficient; calibration required."
            )

        # 4. OOS degradation — sustained over 2 cycles (Sprint 4 single-shot ⇒ pre-emptive).
        if metrics.oos_degradation > t.oos_degradation_max:
            if metrics.consecutive_oos_cycles_failed >= 2:
                violated.append("oos_degradation_sustained")
                recs.append(
                    f"OOS deg {metrics.oos_degradation * 100:.2f}% > "
                    f"{t.oos_degradation_max * 100:.2f}% for 2+ cycles. SHUTDOWN."
                )
            else:
                borderline.append("oos_degradation_high")
                recs.append(
                    f"OOS deg {metrics.oos_degradation * 100:.2f}% > "
                    f"{t.oos_degradation_max * 100:.2f}% (1 cycle); reassess next cycle."
                )

        # 5. Cost-passed band — soft warning unless extreme; SUSPEND only.
        lo, hi = t.cost_passed_band
        if not lo <= metrics.cost_passed_pct <= hi:
            borderline.append("cost_passed_outside_band")
            recs.append(
                f"Cost-passed {metrics.cost_passed_pct * 100:.2f}% outside "
                f"[{lo * 100:.0f}%, {hi * 100:.0f}%]. Re-tune cost or edge model."
            )

        decision = self._decide(violated, borderline, defer_shutdown)
        v031 = self._pivot_eligibility(metrics, violated)
        grace_remaining = max(
            0, t.grace_period_consecutive_days - metrics.consecutive_violation_days
        )

        return KillDecisionResult(
            decision=decision,
            profile=self.profile,
            violated_metrics=tuple(violated),
            borderline_metrics=tuple(borderline),
            recommendations=tuple(recs) if recs else ("All metrics within threshold.",),
            grace_period_remaining=grace_remaining,
            eligible_for_v031_pivot=v031,
            evidence=tuple(sorted(evidence.items())),
            timestamp=datetime.now(tz=UTC),
            reason=_reason(decision, violated, borderline),
        )

    def _decide(
        self,
        violated: list[str],
        borderline: list[str],
        defer_shutdown: bool,
    ) -> KillDecision:
        if violated:
            # INV-GS-033: SHUTDOWN cannot be silently overridden. defer_shutdown
            # downgrades to SUSPEND_7D ONLY when caller passed the explicit flag.
            return KillDecision.SUSPEND_7D if defer_shutdown else KillDecision.SHUTDOWN
        if borderline:
            return KillDecision.SUSPEND_7D
        return KillDecision.CONTINUE

    def _pivot_eligibility(
        self, metrics: HindcastMetricsView, violated: list[str]
    ) -> bool:
        if violated:
            return False
        p = self.pivot_thresholds
        lo, hi = p.cost_passed_band
        compliance_ok = metrics.compliance_clean_days >= self.pivot_compliance_days
        return (
            metrics.sharpe >= p.sharpe_min
            and metrics.oos_degradation <= p.oos_degradation_max
            and metrics.auc >= p.auc_min
            and lo <= metrics.cost_passed_pct <= hi
            and metrics.maxdd <= p.maxdd_max
            and compliance_ok
        )


def _reason(
    decision: KillDecision, violated: list[str], borderline: list[str]
) -> str:
    if decision is KillDecision.SHUTDOWN:
        return f"INV-GS-033: SHUTDOWN — violated={violated}"
    if decision is KillDecision.SUSPEND_7D:
        if violated:
            return f"DEFERRED-SHUTDOWN — caller invoked --defer-shutdown-7d for {violated}"
        return f"SUSPEND_7D — borderline={borderline}; 1 retry allowed"
    return "CONTINUE — all metrics within threshold"


def _load_yaml(path: Path) -> Mapping[str, Any]:
    if not path.exists():
        raise ConfigError(f"kill_criteria.yaml not found at {path}")
    try:
        return yaml.safe_load(path.read_text("utf-8")) or {}
    except yaml.YAMLError as exc:
        raise ConfigError(f"failed to parse {path}: {exc}") from exc


__all__ = [
    "HindcastMetricsView",
    "KillCriteriaMonitor",
    "KillDecision",
    "KillDecisionResult",
    "KillThresholds",
    "ProfileName",
]
