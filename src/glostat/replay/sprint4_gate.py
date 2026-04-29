from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final, Literal

import yaml

from glostat.core.errors import ConfigError
from glostat.replay.kill_criteria import KillThresholds

# Sprint 4 gate evaluation (PLAN_v0.6 + PLAN_v0.5 §3 D2).
# PASS  : all 5 cautious thresholds met → continue to Phase 2 entry decision
# FAIL  : any cautious threshold breached → INV-GS-033 SHUTDOWN
# AMBIG : exactly 1 borderline metric → 1 retry allowed (rerun hindcast w/ extended window)

_KILL_CRITERIA_YAML_DEFAULT: Final[Path] = (
    Path(__file__).resolve().parents[3] / "configs" / "kill_criteria.yaml"
)

GateStatus = Literal["PASS", "FAIL", "AMBIGUOUS"]


@dataclass(frozen=True, slots=True)
class MetricCheck:
    name: str
    actual: float
    threshold: float
    operator: str          # "≥" or "≤" or "∈[lo,hi]"
    passed: bool
    borderline: bool = False
    note: str = ""


@dataclass(frozen=True, slots=True)
class Sprint4Gate:
    pass_status: GateStatus
    profile: str
    per_metric_breakdown: tuple[MetricCheck, ...]
    v031_pivot_eligible: bool
    reasoning: str
    timestamp: datetime
    sample_size: int = 0

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["per_metric_breakdown"] = [asdict(m) for m in self.per_metric_breakdown]
        d["timestamp"] = self.timestamp.isoformat()
        return d

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"))

    @property
    def is_pass(self) -> bool:
        return self.pass_status == "PASS"


@dataclass(slots=True)
class _ProfileBundle:
    profile_name: str
    cautious: KillThresholds
    pivot: KillThresholds
    pivot_compliance_days: int


def _load_profile_bundle(
    profile: str, config_path: Path = _KILL_CRITERIA_YAML_DEFAULT
) -> _ProfileBundle:
    if not config_path.exists():
        raise ConfigError(f"kill_criteria.yaml not found at {config_path}")
    raw = yaml.safe_load(config_path.read_text("utf-8")) or {}
    profiles = raw.get("profiles", {}) or {}
    if profile not in profiles:
        raise ConfigError(
            f"sprint4_gate: profile {profile!r} not in {sorted(profiles)}"
        )
    cautious = KillThresholds.from_mapping(profiles[profile])
    pivot_raw = raw.get("v031_pivot", {}) or {}
    pivot_full = {
        "sharpe_min": pivot_raw.get("sharpe_min", 1.2),
        "oos_degradation_max": pivot_raw.get("oos_degradation_max", 0.15),
        "auc_min": pivot_raw.get("auc_min", 0.65),
        "cost_passed_band": pivot_raw.get("cost_passed_band", [0.50, 0.65]),
        "maxdd_max": pivot_raw.get("maxdd_max", cautious.maxdd_max),
        "grace_period_consecutive_days": 0,
    }
    pivot = KillThresholds.from_mapping(pivot_full)
    return _ProfileBundle(
        profile_name=profile,
        cautious=cautious,
        pivot=pivot,
        pivot_compliance_days=int(pivot_raw.get("compliance_clean_days_required", 90)),
    )


@dataclass(frozen=True, slots=True)
class _GateInputs:
    sharpe: float
    oos_degradation: float
    auc: float
    cost_passed_pct: float
    reproducibility: float
    maxdd: float
    n_verdicts: int = 0
    compliance_clean_days: int = 90


def evaluate_sprint4_gate(
    *,
    sharpe: float,
    oos_degradation: float,
    auc: float,
    cost_passed_pct: float,
    maxdd: float,
    reproducibility: float = 1.0,
    n_verdicts: int = 0,
    compliance_clean_days: int = 90,
    profile: str = "cautious",
    config_path: Path | None = None,
) -> Sprint4Gate:
    bundle = _load_profile_bundle(
        profile, config_path or _KILL_CRITERIA_YAML_DEFAULT
    )
    t = bundle.cautious
    inputs = _GateInputs(
        sharpe=sharpe,
        oos_degradation=oos_degradation,
        auc=auc,
        cost_passed_pct=cost_passed_pct,
        maxdd=maxdd,
        reproducibility=reproducibility,
        n_verdicts=n_verdicts,
        compliance_clean_days=compliance_clean_days,
    )
    checks = _build_checks(inputs, t)
    failed = tuple(c for c in checks if not c.passed)
    borderline_only = tuple(c for c in checks if c.borderline and c.passed)

    if not failed:
        status: GateStatus = "PASS"
        reasoning = "All 5 cautious thresholds met. Sprint 4 gate PASSED."
    elif len(failed) == 1 and failed[0].borderline:
        status = "AMBIGUOUS"
        reasoning = (
            f"1 borderline metric ({failed[0].name}); 1 retry allowed under "
            "INV-GS-033 (rerun hindcast with extended window or recompute pipeline)."
        )
    else:
        status = "FAIL"
        names = ", ".join(c.name for c in failed)
        reasoning = (
            f"INV-GS-033: Sprint 4 gate FAIL — automatic shutdown (no override). "
            f"Failed: {names}"
        )

    pivot_eligible = _v031_eligibility(inputs, bundle, status)
    # WHY: borderline_only is informational; checks already includes every metric.
    _ = borderline_only
    return Sprint4Gate(
        pass_status=status,
        profile=profile,
        per_metric_breakdown=checks,
        v031_pivot_eligible=pivot_eligible,
        reasoning=reasoning,
        timestamp=datetime.now(tz=UTC),
        sample_size=n_verdicts,
    )


def _build_checks(inputs: _GateInputs, t: KillThresholds) -> tuple[MetricCheck, ...]:
    lo, hi = t.cost_passed_band
    sharpe_check = MetricCheck(
        name="sharpe",
        actual=inputs.sharpe,
        threshold=t.sharpe_min,
        operator="≥",
        passed=inputs.sharpe >= t.sharpe_min,
        borderline=_borderline(inputs.sharpe, t.sharpe_min, tol=0.05),
        note=f"actual {inputs.sharpe:.3f} vs threshold {t.sharpe_min:.2f}",
    )
    oos_check = MetricCheck(
        name="oos_degradation",
        actual=inputs.oos_degradation,
        threshold=t.oos_degradation_max,
        operator="≤",
        passed=inputs.oos_degradation <= t.oos_degradation_max,
        borderline=_borderline(
            t.oos_degradation_max, inputs.oos_degradation, tol=0.03
        ),
        note=f"actual {inputs.oos_degradation * 100:.2f}% vs threshold "
        f"{t.oos_degradation_max * 100:.2f}%",
    )
    auc_check = MetricCheck(
        name="auc",
        actual=inputs.auc,
        threshold=t.auc_min,
        operator="≥",
        passed=inputs.auc >= t.auc_min,
        borderline=_borderline(inputs.auc, t.auc_min, tol=0.02),
        note=f"actual {inputs.auc:.3f} vs threshold {t.auc_min:.2f}",
    )
    in_band = lo <= inputs.cost_passed_pct <= hi
    cost_check = MetricCheck(
        name="cost_passed_pct",
        actual=inputs.cost_passed_pct,
        threshold=lo,  # surface the lower bound; band fully described in note
        operator=f"∈[{lo:.2f},{hi:.2f}]",
        passed=in_band,
        borderline=(not in_band)
        and (
            (lo - 0.05 <= inputs.cost_passed_pct < lo)
            or (hi < inputs.cost_passed_pct <= hi + 0.05)
        ),
        note=f"actual {inputs.cost_passed_pct * 100:.2f}% vs band "
        f"[{lo * 100:.0f}%, {hi * 100:.0f}%]",
    )
    repro_check = MetricCheck(
        name="reproducibility",
        actual=inputs.reproducibility,
        threshold=1.0,
        operator="=",
        passed=inputs.reproducibility >= 0.999,  # epsilon for floating-point
        borderline=False,
        note=f"actual {inputs.reproducibility * 100:.2f}% vs threshold 100%",
    )
    return (sharpe_check, oos_check, auc_check, cost_check, repro_check)


def _borderline(a: float, b: float, *, tol: float) -> bool:
    return abs(a - b) <= tol


def _v031_eligibility(
    inputs: _GateInputs, bundle: _ProfileBundle, status: GateStatus
) -> bool:
    if status != "PASS":
        return False
    p = bundle.pivot
    lo, hi = p.cost_passed_band
    compliance_ok = inputs.compliance_clean_days >= bundle.pivot_compliance_days
    return (
        inputs.sharpe >= p.sharpe_min
        and inputs.oos_degradation <= p.oos_degradation_max
        and inputs.auc >= p.auc_min
        and lo <= inputs.cost_passed_pct <= hi
        and inputs.maxdd <= p.maxdd_max
        and compliance_ok
    )


def render_gate_table(gate: Sprint4Gate) -> str:
    lines: list[str] = []
    badge = {"PASS": "[PASS]", "FAIL": "[FAIL]", "AMBIGUOUS": "[AMBIG]"}[gate.pass_status]
    lines.append(
        f"=== Sprint 4 Gate {badge} (profile={gate.profile}, n_verdicts={gate.sample_size}) ==="
    )
    lines.append(
        f"  {'METRIC':<18} {'ACTUAL':>10} {'OP':<5} {'THRESHOLD':>10}  PASS  NOTE"
    )
    lines.append("  " + "-" * 90)
    for c in gate.per_metric_breakdown:
        flag = "OK " if c.passed else "FAIL"
        if c.borderline:
            flag = flag + " (borderline)"
        lines.append(
            f"  {c.name:<18} {c.actual:>10.4f} {c.operator:<5} {c.threshold:>10.4f}  "
            f"{flag:<6}  {c.note}"
        )
    lines.append("")
    lines.append(f"  v0.3.1 pivot eligible : {gate.v031_pivot_eligible}")
    lines.append(f"  reasoning             : {gate.reasoning}")
    return "\n".join(lines)


__all__ = [
    "GateStatus",
    "MetricCheck",
    "Sprint4Gate",
    "evaluate_sprint4_gate",
    "render_gate_table",
]
