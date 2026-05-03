"""v1.10.16 — composite prediction quality: v1.10.3 vs v1.10.15 비교.

질문: INV-GS-133 OOS factor 도입 + retirement + KR thesis 측정 후
실제 composite prediction 품질이 개선됐나?

측정 방법:
- 각 thesis의 OOS Sharpe (실제 측정값) × weight (production 가중치)
- 합산 = "weighted composite OOS performance"
- v1.10.3 (no OOS factor + no retirement) vs v1.10.15 (current) 비교

v1.10.3 시뮬레이션:
- weight = brier_to_weight(brier_score) — OOS factor 없음
- E_FUND_FLOW, E_COMMODITY_TS, E_FUNDING_CARRY 모두 active (retirement 없음)

v1.10.15 (현재):
- weight = _weight_for(cal) = brier × OOS factor + retirement filter
- 3개 retirement는 weight=0

핵심 메트릭:
1. **weighted_oos_sharpe_sum** = Σ(weight × oos_sharpe) — 절대 contribution
2. **stable_weighted_sharpe** = OOS_deg < 50%인 thesis만 합산 (실제 에지)
3. **n_thesis_steering**: 1bp 이상 weight를 가진 thesis 수
4. **stable_dominance**: stable thesis weight 비중

Output: stdout 리포트 + JSON dump
"""
from __future__ import annotations

import json
from typing import Any

from glostat.predictor.calibration import (
    CalibrationTable,
    load_calibration,
)
from glostat.predictor.composite import (
    _brier_to_weight,
    _weight_for,
)


def _v1103_weight(cal: Any) -> float:
    """v1.10.3 weighting: brier only, no OOS factor, no retirement filter."""
    # is_active gate (n>=50, |edge|>0.02) was already in v1.0; just skip
    # the OOS multiplication and retirement check.
    if cal.n_samples < 50:
        return 0.0
    if abs(cal.auc - 0.5) <= 0.02:
        return 0.0
    return _brier_to_weight(cal.brier_score)


def _v1115_weight(cal: Any) -> float:
    """v1.10.15 weighting: brier × OOS factor + retirement filter."""
    return _weight_for(cal)


def _summarize(table: CalibrationTable) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    for name, cal in sorted(table.entries.items()):
        if cal.n_samples == 0:
            continue
        w_v3 = _v1103_weight(cal)
        w_v15 = _v1115_weight(cal)
        rows.append({
            "thesis": name,
            "auc": cal.auc,
            "n_samples": cal.n_samples,
            "sharpe_overall": cal.sharpe,
            "oos_degradation": cal.oos_degradation,
            "is_retired": cal.is_retired,
            "calibration_status": cal.calibration_status,
            "v1103_weight": w_v3,
            "v1115_weight": w_v15,
            "weight_shift": w_v15 - w_v3,
        })
    return {"thesis_rows": rows}


def _aggregate(rows: list[dict[str, Any]]) -> dict[str, Any]:
    # Total weight + steering count under each scheme
    sum_w_v3 = sum(r["v1103_weight"] for r in rows)
    sum_w_v15 = sum(r["v1115_weight"] for r in rows)
    n_steering_v3 = sum(1 for r in rows if r["v1103_weight"] >= 0.001)
    n_steering_v15 = sum(1 for r in rows if r["v1115_weight"] >= 0.001)

    # Weighted contribution = Σ (weight × oos_sharpe). Higher = better
    # composite that allocates to thesis with positive OOS Sharpe.
    # Use IS Sharpe as fallback when OOS missing.
    def _eff_oos_sharpe(r: dict[str, Any]) -> float:
        # If OOS_deg=0 (no degradation), use overall Sharpe as proxy.
        # Else IS sharpe × (1 - oos_deg) approximates OOS sharpe.
        if r["oos_degradation"] <= 0.01:
            return r["sharpe_overall"]
        # If OOS_deg = 1, IS edge fully wiped → OOS ≈ 0
        # If OOS_deg = 2 (extreme), OOS ≈ -overall (sign reversed)
        return r["sharpe_overall"] * max(-1.0, 1.0 - r["oos_degradation"])

    contribution_v3 = sum(
        r["v1103_weight"] * _eff_oos_sharpe(r) for r in rows
    )
    contribution_v15 = sum(
        r["v1115_weight"] * _eff_oos_sharpe(r) for r in rows
    )

    # Stable dominance: weight share of OOS_deg < 50% thesis
    def _stable_share(scheme_key: str) -> float:
        stable_w = sum(r[scheme_key] for r in rows if r["oos_degradation"] < 0.5)
        total_w = sum(r[scheme_key] for r in rows)
        return stable_w / total_w if total_w > 0 else 0.0

    return {
        "sum_weight_v1103": sum_w_v3,
        "sum_weight_v1115": sum_w_v15,
        "n_steering_v1103": n_steering_v3,
        "n_steering_v1115": n_steering_v15,
        "weighted_oos_contribution_v1103": contribution_v3,
        "weighted_oos_contribution_v1115": contribution_v15,
        "contribution_shift": contribution_v15 - contribution_v3,
        "stable_dominance_v1103": _stable_share("v1103_weight"),
        "stable_dominance_v1115": _stable_share("v1115_weight"),
    }


def main() -> int:
    table = load_calibration()
    summary = _summarize(table)
    rows = summary["thesis_rows"]
    agg = _aggregate(rows)

    print("=" * 90)
    print("Composite Quality: v1.10.3 (no OOS factor / no retirement) vs v1.10.15 (current)")
    print("=" * 90)
    print()
    hdr = (
        f"{'thesis':<28} {'AUC':>5} {'OOSdeg':>8} {'Sharpe':>7} "
        f"{'w_v3':>6} {'w_v15':>7} {'Δw':>7} "
        f"{'cont_v3':>10} {'cont_v15':>10}"
    )
    print(hdr)
    print("-" * 105)
    for r in sorted(
        rows, key=lambda x: -max(x["v1103_weight"], x["v1115_weight"]),
    ):
        oos_s = (
            r["sharpe_overall"] * max(-1.0, 1.0 - r["oos_degradation"])
            if r["oos_degradation"] > 0.01
            else r["sharpe_overall"]
        )
        print(
            f'{r["thesis"]:<28} {r["auc"]:>5.3f} '
            f'{r["oos_degradation"]:>7.1%} {r["sharpe_overall"]:>+7.3f} '
            f'{r["v1103_weight"]:>6.3f} {r["v1115_weight"]:>7.4f} '
            f'{r["weight_shift"]:>+7.4f} '
            f'{r["v1103_weight"] * oos_s:>+10.4f} '
            f'{r["v1115_weight"] * oos_s:>+10.4f}'
        )

    print()
    print("=" * 90)
    print("Aggregate")
    print("=" * 90)
    w3 = agg["sum_weight_v1103"]
    w15 = agg["sum_weight_v1115"]
    print(f"  Total weight (v1.10.3 / v1.10.15): {w3:.4f} / {w15:.4f}")
    print(f"    → reduction: {(1 - w15 / max(w3, 1e-9)):.1%}")
    n3, n15 = agg["n_steering_v1103"], agg["n_steering_v1115"]
    print(f"  Theses steering (≥0.001 weight): {n3} → {n15}")
    print()
    print("  Weighted OOS contribution (Σ weight × effective_oos_sharpe):")
    print(f"    v1.10.3:  {agg['weighted_oos_contribution_v1103']:+.4f}")
    print(f"    v1.10.15: {agg['weighted_oos_contribution_v1115']:+.4f}")
    print(f"    shift:    {agg['contribution_shift']:+.4f}")
    print()
    print("  Stable dominance (OOS_deg<50% weight 비중):")
    print(f"    v1.10.3:  {agg['stable_dominance_v1103']:.1%}")
    print(f"    v1.10.15: {agg['stable_dominance_v1115']:.1%}")
    print()

    # Output JSON
    print("JSON output (for audit doc):")
    print(json.dumps({"thesis_rows": rows, "aggregate": agg}, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
