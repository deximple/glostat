from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from glostat.predictor.calibration import (
    CalibrationTable,
    ThesisCalibration,
    is_active,
    load_calibration,
    synthetic_calibration_for_mock,
)


def _make_thesis(
    *, name: str = "X", auc: float = 0.55, sharpe: float = 0.5,
    n: int = 200, oos_deg: float = 0.1,
) -> ThesisCalibration:
    return ThesisCalibration(
        name=name, auc=auc, sharpe=sharpe, n_samples=n,
        oos_degradation=oos_deg,
        period_start=date(2024, 1, 1), period_end=date(2026, 3, 31),
    )


# ── ThesisCalibration unit ────────────────────────────────────────────────


def test_brier_score_zero_for_perfect_auc() -> None:
    t = _make_thesis(auc=1.0, n=10000)
    # AUC=1 → edge=0.5 → raw=0.25*(1-1)=0 → sample_penalty=1 → no adjustment
    assert t.brier_score == pytest.approx(0.0, abs=1e-9)


def test_brier_score_max_for_random_auc() -> None:
    t = _make_thesis(auc=0.5, n=1000)
    assert t.brier_score == pytest.approx(0.25, abs=1e-9)


def test_brier_penalises_small_n() -> None:
    big = _make_thesis(auc=0.6, n=1000)
    small = _make_thesis(auc=0.6, n=10)
    assert small.brier_score > big.brier_score


def test_directional_bias_above_random() -> None:
    assert _make_thesis(auc=0.6).directional_bias == +1


def test_directional_bias_below_random() -> None:
    assert _make_thesis(auc=0.4).directional_bias == -1


def test_directional_bias_at_random() -> None:
    assert _make_thesis(auc=0.5).directional_bias == 0


# ── is_active ─────────────────────────────────────────────────────────────


def test_is_active_requires_min_samples() -> None:
    t = _make_thesis(auc=0.7, n=10)
    assert is_active(t) is False


def test_is_active_requires_auc_delta() -> None:
    t = _make_thesis(auc=0.51, n=200)
    assert is_active(t) is False


def test_is_active_passes_typical_thesis() -> None:
    t = _make_thesis(auc=0.586, n=298)
    assert is_active(t) is True


def test_is_active_under_random_auc_passes() -> None:
    # WHY: |auc - 0.5| > 0.02 — under-random theses still informative when flipped.
    t = _make_thesis(auc=0.339, n=200)
    assert is_active(t) is True


# ── CalibrationTable.get fallback ─────────────────────────────────────────


def test_calibration_table_get_missing_returns_random_default() -> None:
    table = CalibrationTable()
    cal = table.get("E_NONEXISTENT")
    assert cal.auc == pytest.approx(0.5)
    assert cal.n_samples == 0
    assert is_active(cal) is False


def test_calibration_table_get_existing() -> None:
    table = CalibrationTable()
    table.entries["FOO"] = _make_thesis(name="FOO", auc=0.6)
    cal = table.get("FOO")
    assert cal.auc == pytest.approx(0.6)


def test_calibration_table_names() -> None:
    table = CalibrationTable()
    table.entries["B"] = _make_thesis(name="B")
    table.entries["A"] = _make_thesis(name="A")
    assert table.names() == ("A", "B")


# ── synthetic_calibration_for_mock — covers all 11 theses ────────────────


def test_synthetic_calibration_has_twentytwo_theses() -> None:
    # v1.10: 22 prior + E_REGIME_US = 23.
    table = synthetic_calibration_for_mock()
    assert len(table.entries) == 23
    assert "E_FUNDAMENTAL_KR" in table.entries
    assert "E_TIME_KR" in table.entries
    assert "E_INSIDER_KR" in table.entries
    assert "E_MACRO_KR" in table.entries
    assert "E_SHORT_SELLING_KR" in table.entries
    assert "E_INTRADAY_FLOW_KR" in table.entries
    assert "E_FUNDAMENTAL_KR_CYCLICAL" in table.entries
    assert "E_COMMODITY_INDEX_KR" in table.entries
    assert "E_PEAD_KR" in table.entries
    assert "E_REGIME_US" in table.entries


def test_synthetic_includes_phase1b_theses() -> None:
    table = synthetic_calibration_for_mock()
    for n in (
        "E_FUNDAMENTAL", "E_TIME", "E_FUND_FLOW",
        "E_SECTOR_ROTATION", "E_PEAD", "E_FOMC_DRIFT",
        "E_INSIDER_CLUSTER",
    ):
        assert n in table.entries


def test_synthetic_includes_phase1c_theses() -> None:
    table = synthetic_calibration_for_mock()
    assert "E_COMMODITY_TS" in table.entries
    assert "E_FX_CARRY" in table.entries


def test_synthetic_includes_phase1d_theses() -> None:
    table = synthetic_calibration_for_mock()
    assert "E_FUNDING_CARRY" in table.entries
    assert "E_FOREIGN_REVERSAL" in table.entries


def test_synthetic_pead_matches_archived_metrics() -> None:
    table = synthetic_calibration_for_mock()
    pead = table.entries["E_PEAD"]
    assert pead.auc == pytest.approx(0.586, abs=0.001)
    assert pead.sharpe == pytest.approx(0.629, abs=0.001)
    assert pead.n_samples == 298


# ── load_calibration — empty cache returns empty table ────────────────────


def test_load_calibration_empty_cache(tmp_path: Path) -> None:
    # v1.10: 23 entries = 22 prior + E_REGIME_US (skeleton).
    table = load_calibration(cache_dir=tmp_path)
    assert len(table.entries) == 23
    assert "E_FUNDAMENTAL_KR" in table.entries
    assert "E_TIME_KR" in table.entries
    assert "E_INSIDER_KR" in table.entries
    assert "E_MACRO_KR" in table.entries
    assert "E_SHORT_SELLING_KR" in table.entries
    assert "E_INTRADAY_FLOW_KR" in table.entries
    assert "E_FUNDAMENTAL_KR_CYCLICAL" in table.entries
    assert "E_COMMODITY_INDEX_KR" in table.entries
    assert "E_PEAD_KR" in table.entries


def test_load_calibration_reads_phase1b_report(tmp_path: Path) -> None:
    phase1b = tmp_path / "phase1b"
    phase1b.mkdir()
    (phase1b / "e_pead_report.json").write_text(json.dumps({
        "report": {
            "expert": "E_PEAD",
            "n_trades": 298,
            "is_sharpe": 0.98,
            "oos_sharpe": -0.15,
            "overall_sharpe": 0.629,
            "is_auc": 0.62,
            "oos_auc": 0.54,
            "overall_auc": 0.586,
        }
    }))
    table = load_calibration(cache_dir=tmp_path)
    assert "E_PEAD" in table.entries
    pead = table.entries["E_PEAD"]
    assert pead.auc == pytest.approx(0.586, abs=0.001)
    assert pead.n_samples == 298


def test_load_calibration_reads_phase1c_report(tmp_path: Path) -> None:
    hindcast = tmp_path / "hindcast"
    hindcast.mkdir()
    (hindcast / "phase1c_fx_carry_report.json").write_text(json.dumps({
        "expert": "E_FX_CARRY",
        "n_trades": 135,
        "is_sharpe": -1.16,
        "oos_sharpe": -2.79,
        "overall_sharpe": -1.533,
        "is_auc": 0.40,
        "oos_auc": 0.38,
        "overall_auc": 0.400,
    }))
    table = load_calibration(cache_dir=tmp_path)
    assert "E_FX_CARRY" in table.entries
    fxc = table.entries["E_FX_CARRY"]
    assert fxc.auc == pytest.approx(0.4, abs=0.001)
    assert fxc.n_samples == 135


def test_load_calibration_reads_phase1d_md(tmp_path: Path) -> None:
    phase1d = tmp_path / "hindcast" / "phase1d"
    phase1d.mkdir(parents=True)
    (phase1d / "phase1d_comparison.md").write_text(
        "| metric | E7 Funding Carry | E9 KR Reversal |\n"
        "|---|---:|---:|\n"
        "| traded (post-cost) | 2921 | 418 |\n"
        "| Sharpe (overall) | -0.2314 | 0.5834 |\n"
        "| Sharpe IS | 0.6060 | 0.1803 |\n"
        "| Sharpe OOS | -2.1660 | 1.4627 |\n"
        "| AUC (overall) | 0.5052 | 0.4667 |\n"
    )
    table = load_calibration(cache_dir=tmp_path)
    assert "E_FUNDING_CARRY" in table.entries
    assert "E_FOREIGN_REVERSAL" in table.entries
    assert table.entries["E_FUNDING_CARRY"].auc == pytest.approx(0.5052, abs=0.001)
    assert table.entries["E_FOREIGN_REVERSAL"].auc == pytest.approx(0.4667, abs=0.001)


# ── snapshot writer ──────────────────────────────────────────────────────


def test_calibration_table_writes_parquet_or_json(tmp_path: Path) -> None:
    table = synthetic_calibration_for_mock()
    out = tmp_path / "calibration.parquet"
    written = table.write_parquet(out)
    assert written.exists()
    # Either parquet or json fallback — both are acceptable.


def test_calibration_table_to_records_round_trip() -> None:
    table = synthetic_calibration_for_mock()
    records = table.to_records()
    # v1.10: 22 prior + E_REGIME_US = 23.
    assert len(records) == 23
    assert all("brier_score" in r for r in records)
    assert all("auc" in r for r in records)
