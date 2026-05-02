"""Measure prediction-output impact of INV-GS-133 OOS-stability factor.

Quantifies the v1.10.3 → v1.10.4 transition: composite weights now include
`max(0.10, 1 - 0.9*clip(oos_deg, 0, 1))` as a multiplier. This script
constructs deterministic synthetic predictions across all measured/active
theses and reports per-scenario:

  - p_up under v1.10.3 weighting (no OOS factor)
  - p_up under v1.10.4 weighting (current; with OOS factor)
  - |Δp_up| (absolute shift)
  - edge_over_baseline shift (pp)
  - CI width shift (bps)

Three scenarios:
  - bullish: every active LONG-bias thesis fires LONG with strong score
  - bearish: every active LONG-bias thesis fires SHORT with strong score
  - mixed:   IS-only-edge theses (E_PEAD, E_FOMC_DRIFT, E_FX_CARRY) fire LONG;
             stable-edge theses fire NEUTRAL → measures pure OOS-suppression
             impact

Output: stdout report + JSON dump for audit doc inclusion.
"""
from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any
from unittest.mock import patch

from glostat.predictor.calibration import (
    CalibrationTable,
    is_active,
    synthetic_calibration_for_mock,
)
from glostat.predictor.composite import predict
from glostat.predictor.types import SignalContribution


def _all_active_theses(table: CalibrationTable) -> list[str]:
    return [name for name, cal in table.entries.items() if is_active(cal)]


def _signal(
    *, name: str, value: float, direction: str, table: CalibrationTable,
) -> SignalContribution:
    cal = table.entries[name]
    return SignalContribution(
        name=name, value=value, direction=direction,  # type: ignore[arg-type]
        calibration_auc=cal.auc,
        calibration_sharpe=cal.sharpe,
        n_samples=cal.n_samples,
    )


def _build_scenario(
    *, name: str, table: CalibrationTable,
) -> tuple[SignalContribution, ...]:
    active = _all_active_theses(table)
    out: list[SignalContribution] = []
    is_only_edge_theses = {
        "E_PEAD", "E_FOMC_DRIFT", "E_FX_CARRY", "E_SECTOR_ROTATION",
    }
    for thesis in active:
        if name == "bullish":
            out.append(_signal(
                name=thesis, value=1.5, direction="up", table=table,
            ))
        elif name == "bearish":
            out.append(_signal(
                name=thesis, value=-1.5, direction="down", table=table,
            ))
        elif name == "mixed":
            # Only IS-only-edge theses fire — measures pure OOS-suppression
            # impact (these are the theses INV-GS-133 was designed to
            # suppress).
            if thesis in is_only_edge_theses:
                out.append(_signal(
                    name=thesis, value=2.0, direction="up", table=table,
                ))
            else:
                out.append(_signal(
                    name=thesis, value=0.0, direction="neutral", table=table,
                ))
    return tuple(out)


def _run_one_scenario(
    *, scenario: str, table: CalibrationTable,
) -> dict[str, Any]:
    contribs = _build_scenario(name=scenario, table=table)
    issued = datetime(2026, 5, 3, 12, 0, tzinfo=UTC)

    # v1.10.4 (current): OOS factor active
    pred_v4 = predict(
        ticker="SYNTH", horizon="swing_30d",
        contributions=contribs, cal_table=table, issued_at=issued,
    )

    # v1.10.3: monkey-patch _oos_stability_factor to return 1.0 so the
    # weighting collapses back to pure brier weighting.
    with patch(
        "glostat.predictor.composite._oos_stability_factor",
        return_value=1.0,
    ):
        pred_v3 = predict(
            ticker="SYNTH", horizon="swing_30d",
            contributions=contribs, cal_table=table, issued_at=issued,
        )

    return {
        "scenario": scenario,
        "v1103_no_oos_factor": {
            "p_up": pred_v3.up_probability,
            "p_down": pred_v3.down_probability,
            "p_neutral": pred_v3.sideways_probability,
            "edge_pp": pred_v3.edge_over_baseline_pp,
            "expected_bps": pred_v3.expected_return_bps,
            "ci_width_bps": (
                pred_v3.confidence_interval_bps[1]
                - pred_v3.confidence_interval_bps[0]
            ),
        },
        "v1104_with_oos_factor": {
            "p_up": pred_v4.up_probability,
            "p_down": pred_v4.down_probability,
            "p_neutral": pred_v4.sideways_probability,
            "edge_pp": pred_v4.edge_over_baseline_pp,
            "expected_bps": pred_v4.expected_return_bps,
            "ci_width_bps": (
                pred_v4.confidence_interval_bps[1]
                - pred_v4.confidence_interval_bps[0]
            ),
        },
        "delta": {
            "p_up": pred_v4.up_probability - pred_v3.up_probability,
            "edge_pp": (
                pred_v4.edge_over_baseline_pp - pred_v3.edge_over_baseline_pp
            ),
            "expected_bps": (
                pred_v4.expected_return_bps - pred_v3.expected_return_bps
            ),
        },
    }


def main() -> int:
    table = synthetic_calibration_for_mock()
    active = _all_active_theses(table)

    print("=" * 70)
    print("OOS Stability Factor (INV-GS-133) Before/After Impact")
    print("=" * 70)
    print(f"Active theses considered: {len(active)}")
    for t in active:
        cal = table.entries[t]
        print(f"  {t:<24} AUC={cal.auc:.3f} OOS_deg={cal.oos_degradation:.2%}")
    print()

    results = []
    for scenario in ("bullish", "bearish", "mixed"):
        result = _run_one_scenario(scenario=scenario, table=table)
        results.append(result)
        print(f"--- scenario: {scenario} ---")
        v3 = result["v1103_no_oos_factor"]
        v4 = result["v1104_with_oos_factor"]
        delta = result["delta"]
        print(
            f"  v1.10.3 (no OOS factor): p_up={v3['p_up']:.4f}  "
            f"edge={v3['edge_pp']:+.2f}pp  E[r]={v3['expected_bps']:+.1f}bps  "
            f"CI={v3['ci_width_bps']:.1f}bps"
        )
        print(
            f"  v1.10.4 (with OOS factor): p_up={v4['p_up']:.4f}  "
            f"edge={v4['edge_pp']:+.2f}pp  E[r]={v4['expected_bps']:+.1f}bps  "
            f"CI={v4['ci_width_bps']:.1f}bps"
        )
        print(
            f"  delta: Δp_up={delta['p_up']:+.4f}  "
            f"Δedge={delta['edge_pp']:+.2f}pp  "
            f"ΔE[r]={delta['expected_bps']:+.1f}bps"
        )
        print()

    # Aggregate stats
    abs_dp = [abs(r["delta"]["p_up"]) for r in results]
    print("=== Aggregate ===")
    print(f"  Mean |Δp_up| across 3 scenarios: {sum(abs_dp) / len(abs_dp):.4f}")
    print(f"  Max  |Δp_up|: {max(abs_dp):.4f}")
    print()

    # Per-thesis weight comparison
    from glostat.predictor.composite import (  # noqa: PLC0415
        _brier_to_weight,
        _weight_for,
    )

    print("=== Per-thesis weight comparison ===")
    print(f'{"thesis":<28} {"v1.10.3 (brier)":>16} {"v1.10.4 (final)":>16} {"shift":>8}')
    print("-" * 76)
    weight_changes = []
    for thesis in active:
        cal = table.entries[thesis]
        w_v3 = _brier_to_weight(cal.brier_score)
        w_v4 = _weight_for(cal)
        shift = w_v4 - w_v3
        weight_changes.append((thesis, w_v3, w_v4, shift))
        print(f"{thesis:<28} {w_v3:>16.4f} {w_v4:>16.4f} {shift:>+8.4f}")

    print()
    print("Output JSON for audit doc:")
    print(json.dumps({
        "scenarios": results,
        "weight_changes": [
            {"thesis": t, "v1103_brier": w3, "v1104_final": w4, "shift": s}
            for t, w3, w4, s in weight_changes
        ],
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
