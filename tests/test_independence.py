"""Tests for glostat.predictor.independence — effective rank / resume gate."""

from __future__ import annotations

import numpy as np
import pytest

from glostat.predictor.independence import (
    ResumeVerdict,
    effective_rank,
    effective_rank_from_returns,
    resume_gate,
    returns_matrix_from_records,
)


def test_effective_rank_identity_is_full():
    """Orthogonal series (identity correlation) -> effective rank == K."""
    assert effective_rank(np.eye(3)) == pytest.approx(3.0)
    assert effective_rank(np.eye(5)) == pytest.approx(5.0)


def test_effective_rank_collinear_is_one():
    """All-ones correlation (perfect collinearity) -> effective rank == 1."""
    C = np.ones((4, 4))
    assert effective_rank(C) == pytest.approx(1.0)


def test_effective_rank_two_correlated_one_independent():
    """Two theses at rho=0.82 + one independent ~ 2 effective dimensions."""
    C = np.array([
        [1.00, 0.82, 0.00],
        [0.82, 1.00, 0.00],
        [0.00, 0.00, 1.00],
    ])
    er = effective_rank(C)
    # the 0.82 pair collapses toward 1 dim; total well under 3, around 2.2
    assert 1.8 < er < 2.4


def test_effective_rank_edge_cases():
    assert effective_rank(np.zeros((0, 0))) == 0.0
    assert effective_rank(np.array([[1.0]])) == 1.0
    with pytest.raises(ValueError):
        effective_rank(np.zeros((2, 3)))


def test_effective_rank_from_returns_independent():
    """Independent random columns -> effective rank near K."""
    rng = np.random.default_rng(7)
    R = rng.standard_normal((500, 4))
    er = effective_rank_from_returns(R)
    assert er > 3.4  # close to 4 for genuinely independent series


def test_effective_rank_from_returns_duplicated_column():
    """A duplicated series adds no independent dimension."""
    rng = np.random.default_rng(11)
    base = rng.standard_normal((400, 1))
    indep = rng.standard_normal((400, 1))
    R = np.hstack([base, base.copy(), indep])  # col0 == col1
    er = effective_rank_from_returns(R)
    assert er < 2.3  # ~2 effective dims despite 3 columns


def test_effective_rank_disjoint_dates_is_unmeasurable_not_full_rank():
    """3 theses on disjoint dates → no common rows → fail-closed 0.0, NOT float(K).

    Each thesis has its own 2 dates; aligned matrix has NaN in every row, so after
    the NaN-row drop zero rows remain. Must return 0.0 (unmeasurable), not 3.0."""
    import numpy as _np
    # build a (6 x 3) matrix where each column is non-constant but no row is complete
    M = _np.array([
        [1.0, _np.nan, _np.nan],
        [-1.0, _np.nan, _np.nan],
        [_np.nan, 1.0, _np.nan],
        [_np.nan, -1.0, _np.nan],
        [_np.nan, _np.nan, 1.0],
        [_np.nan, _np.nan, -1.0],
    ])
    er = effective_rank_from_returns(M)
    assert er == 0.0  # unmeasurable → fail-closed, not full rank


def test_effective_rank_from_returns_drops_constant_column():
    rng = np.random.default_rng(3)
    a = rng.standard_normal((200, 1))
    b = rng.standard_normal((200, 1))
    const = np.full((200, 1), 0.5)
    R = np.hstack([a, b, const])
    er = effective_rank_from_returns(R)
    assert er > 1.6  # constant column dropped; ~2 from a,b


def test_resume_gate_fails_on_too_few_significant():
    rng = np.random.default_rng(1)
    R = rng.standard_normal((300, 3))
    p = [0.01, 0.20, 0.30]  # only 1 significant
    v = resume_gate(p, R)
    assert isinstance(v, ResumeVerdict)
    assert v.eligible is False
    assert v.n_significant == 1


def test_resume_gate_fails_on_correlated_significant():
    """3 significant theses but two are rho=0.82 -> effective rank < 3."""
    rng = np.random.default_rng(2)
    a = rng.standard_normal((600, 1))
    noise = rng.standard_normal((600, 1))
    b = 0.82 * a + np.sqrt(1 - 0.82 ** 2) * noise  # corr(a,b) ~ 0.82
    c = rng.standard_normal((600, 1))
    R = np.hstack([a, b, c])
    p = [0.01, 0.01, 0.02]  # all significant
    v = resume_gate(p, R)
    assert v.n_significant == 3
    assert v.effective_rank < 3.0
    assert v.eligible is False


def test_resume_gate_passes_on_independent_significant():
    rng = np.random.default_rng(4)
    R = rng.standard_normal((600, 3))
    p = [0.01, 0.02, 0.04]  # all significant, independent
    v = resume_gate(p, R)
    assert v.n_significant == 3
    assert v.effective_rank > 2.8
    assert v.eligible is True


def test_resume_gate_column_alignment_validation():
    R = np.zeros((10, 2))
    with pytest.raises(ValueError):
        resume_gate([0.01, 0.02, 0.03], R)  # 3 p-values, 2 columns


# ── returns_matrix_from_records (hindcast adapter) ───────────────────────────


def test_returns_matrix_pivots_and_sorts():
    records = [
        ("E_B", "2026-01-01", 1.0),
        ("E_A", "2026-01-01", 2.0),
        ("E_A", "2026-01-02", 3.0),
        ("E_B", "2026-01-02", 4.0),
    ]
    names, dates, m = returns_matrix_from_records(records)
    assert names == ["E_A", "E_B"]            # sorted
    assert dates == ["2026-01-01", "2026-01-02"]
    assert m.shape == (2, 2)
    assert m[0, 0] == 2.0 and m[0, 1] == 1.0  # date0: A=2, B=1
    assert m[1, 0] == 3.0 and m[1, 1] == 4.0


def test_returns_matrix_averages_duplicates():
    records = [("E_A", "d1", 2.0), ("E_A", "d1", 4.0), ("E_B", "d1", 1.0)]
    names, _, m = returns_matrix_from_records(records)
    a = names.index("E_A")
    assert m[0, a] == pytest.approx(3.0)  # mean(2,4)


def test_returns_matrix_missing_cells_are_nan():
    records = [("E_A", "d1", 1.0), ("E_B", "d2", 2.0)]  # disjoint dates
    _, _, m = returns_matrix_from_records(records)
    assert np.isnan(m).sum() == 2  # the two missing cells


def test_returns_matrix_feeds_effective_rank_on_common_dates():
    # E_A and E_B move together on shared dates -> effective rank ~1
    recs = []
    for i, d in enumerate(["d1", "d2", "d3", "d4", "d5", "d6"]):
        v = 1.0 if i % 2 == 0 else -1.0
        recs.append(("E_A", d, v))
        recs.append(("E_B", d, v))       # identical to E_A
        recs.append(("E_C", d, -v))      # perfectly anti-correlated (still 1 dim)
    _, _, m = returns_matrix_from_records(recs)
    assert m.shape == (6, 3)
    assert effective_rank_from_returns(m) < 1.5  # all collinear → ~1 dimension
