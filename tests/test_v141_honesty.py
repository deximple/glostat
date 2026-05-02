from __future__ import annotations

import math
import subprocess
import sys
from pathlib import Path
from typing import Final

import pytest

from glostat.predictor.honesty import (
    all_active_signals_are_noise,
    auc_p_value,
    auc_standard_error,
    auc_z_score,
    ci_includes_zero,
    format_significance,
    is_statistically_significant,
    kr_megacap_honesty_note,
    round_trip_bps,
)

# v1.4.1 X+W honesty patch — INV-GS-113 (statistical disclosure) +
# INV-GS-114 (KR megacap universe-specific honesty).
# Panel synthesis: P8 Statistician + P10 Contrarian Veteran.

_CLI_MODULE: Final = "glostat.cli"


# ── INV-GS-113: statistical-significance helpers ──────────────────────────


class TestAucStandardError:
    def test_zero_n_returns_inf(self) -> None:
        assert math.isinf(auc_standard_error(0))

    def test_n_3510_matches_panel_quote(self) -> None:
        # P8 Statistician reported SE ≈ 0.0049 for E_FUNDAMENTAL_KR n=3510.
        se = auc_standard_error(3510)
        assert abs(se - 0.00487) < 1e-3

    def test_n_200_matches_panel_quote(self) -> None:
        # P8 reported SE ≈ 0.0204 for E_TIME n=200.
        se = auc_standard_error(200)
        assert abs(se - 0.0204) < 1e-3


class TestAucZScore:
    def test_perfect_discrimination_large_z(self) -> None:
        z = auc_z_score(0.95, n=1000)
        assert z > 30.0  # very far from null

    def test_random_auc_zero_z(self) -> None:
        assert abs(auc_z_score(0.5, n=1000)) < 1e-9

    def test_n_zero_returns_zero(self) -> None:
        assert auc_z_score(0.95, n=0) == 0.0

    def test_e_fundamental_kr_z_matches_panel(self) -> None:
        # P8 reported z = (0.495 - 0.500) / 0.00487 = -1.02
        z = auc_z_score(0.495, n=3510)
        assert abs(z - (-1.02)) < 0.05


class TestAucPValue:
    def test_random_auc_p_one(self) -> None:
        # AUC exactly 0.5 → z=0 → p = 1.0.
        assert auc_p_value(0.5, n=1000) == pytest.approx(1.0)

    def test_n_zero_returns_one(self) -> None:
        assert auc_p_value(0.6, n=0) == 1.0

    def test_strong_signal_low_p(self) -> None:
        # AUC 0.7 with n=500 should be highly significant.
        assert auc_p_value(0.7, n=500) < 0.001

    def test_panel_p_value_for_e_fundamental_kr(self) -> None:
        # P8 reported p=0.31 for AUC=0.495, n=3510.
        p = auc_p_value(0.495, n=3510)
        assert 0.25 < p < 0.35


class TestIsStatisticallySignificant:
    def test_random_auc_not_sig(self) -> None:
        assert not is_statistically_significant(0.495, 3510)

    def test_n_zero_not_sig(self) -> None:
        assert not is_statistically_significant(0.95, 0)

    def test_strong_signal_sig(self) -> None:
        assert is_statistically_significant(0.7, 500)


class TestFormatSignificance:
    def test_n_zero_returns_no_data(self) -> None:
        assert format_significance(0.5, 0) == "no data"

    def test_random_returns_n_s(self) -> None:
        tag = format_significance(0.495, 3510)
        assert "n.s." in tag

    def test_strong_returns_lt_001(self) -> None:
        tag = format_significance(0.7, 500)
        assert tag.startswith("p<0.")


# ── X4: round-trip cost lookup from markets.yaml ──────────────────────────


class TestRoundTripBps:
    def test_xkrx_around_22_bps(self) -> None:
        # markets.yaml: fee=1.5, tax_sell=20.0, tax_buy=0 → 1.5+1.5+0+20 = 23
        cost = round_trip_bps("XKRX")
        assert 22.0 <= cost <= 24.0

    def test_xnas_under_2_bps(self) -> None:
        # markets.yaml: fee=0.6, tax_sell=0.24, tax_buy=0 → 0.6+0.6+0+0.24 = 1.44
        cost = round_trip_bps("XNAS")
        assert 1.0 < cost < 2.0

    def test_unknown_market_zero(self) -> None:
        assert round_trip_bps("XNONEXISTENT") == 0.0


# ── X1 / utility: CI includes-0 detection ─────────────────────────────────


class TestCiIncludesZero:
    def test_strictly_positive_excludes_zero(self) -> None:
        assert not ci_includes_zero(10.0, 50.0)

    def test_strictly_negative_excludes_zero(self) -> None:
        assert not ci_includes_zero(-50.0, -10.0)

    def test_spans_zero(self) -> None:
        assert ci_includes_zero(-40.0, 123.0)

    def test_low_at_zero(self) -> None:
        assert ci_includes_zero(0.0, 50.0)


# ── X6: composite all-noise detector ──────────────────────────────────────


class TestAllActiveSignalsAreNoise:
    def test_empty_returns_true(self) -> None:
        assert all_active_signals_are_noise(())

    def test_all_random_returns_true(self) -> None:
        # All AUC near 0.5 with mid-size n: every signal is n.s.
        sigs = ((0.495, 3510), (0.520, 200), (0.464, 138))
        assert all_active_signals_are_noise(sigs)

    def test_one_strong_returns_false(self) -> None:
        sigs = ((0.495, 3510), (0.7, 500))
        assert not all_active_signals_are_noise(sigs)


# ── INV-GS-114: KR megacap universe-specific honesty footer ──────────────


class TestKrMegacapHonestyNote:
    def test_xkrx_returns_note(self) -> None:
        note = kr_megacap_honesty_note("XKRX")
        assert note is not None
        assert "KR megacap" in note
        assert "AUC" in note

    def test_xkos_returns_note(self) -> None:
        note = kr_megacap_honesty_note("XKOS")
        assert note is not None
        assert "KOSPI" in note or "Phase KR" in note

    def test_xnas_returns_none(self) -> None:
        assert kr_megacap_honesty_note("XNAS") is None

    def test_xnys_returns_none(self) -> None:
        assert kr_megacap_honesty_note("XNYS") is None


# ── End-to-end: CLI output reflects the new honesty annotations ──────────


@pytest.fixture
def workdir(tmp_path: Path) -> Path:
    return tmp_path


def _run(*args: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", _CLI_MODULE, *args],
        cwd=cwd, capture_output=True, text=True, check=False, timeout=30,
    )


class TestCliHonestyOutput:
    def test_us_predict_emits_one_sigma_label(self, workdir: Path) -> None:
        r = _run("predict", "AAPL", "--mock", cwd=workdir)
        assert r.returncode == 0
        assert "CI 1-sigma (68%)" in r.stdout

    def test_us_predict_does_not_emit_kr_note(self, workdir: Path) -> None:
        # XNAS is the default mock market — KR note must NOT appear.
        r = _run("predict", "AAPL", "--mock", cwd=workdir)
        assert r.returncode == 0
        assert "KR megacap" not in r.stdout

    def test_signal_line_carries_p_value_or_no_data(
        self, workdir: Path
    ) -> None:
        r = _run("predict", "AAPL", "--mock", cwd=workdir)
        assert r.returncode == 0
        # At least one of: explicit p-value tag OR explicit "no data" line.
        assert ("p=" in r.stdout) or ("no data" in r.stdout)
