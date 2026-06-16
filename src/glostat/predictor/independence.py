"""Thesis independence — effective rank / participation ratio.

The resume condition is ">= 3 INDEPENDENT theses each at p<0.05". Today the
count leg (p<0.05) is machine-checked (honesty.is_statistically_significant)
but "INDEPENDENT" is enforced only by a human reading the word — composite.py
has zero correlation handling. The 5/6-noise disaster and correlated-sleeve
findings (e.g. ranker<->SSF rho≈+0.82) are the same failure: counting
correlated bets as independent.

This module supplies the missing, reusable independence math used by the
resume gate (and, later, by per-strategy fragility scoring): the EFFECTIVE
RANK of a set of return series via the participation ratio of the correlation
matrix's eigenvalues:

    PR = (sum lambda_i)^2 / sum(lambda_i^2)

For a K x K correlation matrix the eigenvalues sum to K, so PR ranges from 1
(all series perfectly collinear -> one effective dimension) to K (mutually
orthogonal -> K effective dimensions). Two theses at rho=0.82 contribute well
under 2 effective dimensions, so they cannot pass a "3 independent" gate.

Pure module: numpy only, no I/O, no mutation. Wiring this to the live resume
gate requires a per-thesis signed-return matrix (T periods x K theses), which
the scalar CalibrationTable does not carry — that series must come from the
hindcast layer and be passed in.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass

import numpy as np

_ZERO_VAR_EPS = 1e-12


def effective_rank(corr: np.ndarray) -> float:
    """Participation ratio of a correlation (or covariance) matrix's spectrum.

    Returns a value in [0, K]: ~K when the K series are mutually orthogonal,
    ~1 when they are perfectly collinear. Robust to tiny negative eigenvalues
    from numerical error.
    """
    cm = np.asarray(corr, dtype=float)
    if cm.ndim != 2 or cm.shape[0] != cm.shape[1]:
        raise ValueError("corr must be a square 2-D matrix")
    k = cm.shape[0]
    if k == 0:
        return 0.0
    if k == 1:
        return 1.0
    eig = np.linalg.eigvalsh(cm)
    eig = np.clip(eig, 0.0, None)  # numerical hygiene: drop tiny negatives
    s1 = float(eig.sum())
    s2 = float((eig**2).sum())
    if s2 <= 0.0:
        return 0.0
    return (s1 * s1) / s2


def effective_rank_from_returns(returns: np.ndarray) -> float:
    """Effective rank of a (T periods x K theses) return matrix.

    Drops near-zero-variance columns (a constant series carries no independent
    information) and rows with any NaN before computing the correlation matrix.
    Returns the participation ratio of the resulting correlation spectrum.
    """
    mat = np.asarray(returns, dtype=float)
    if mat.ndim != 2:
        raise ValueError("returns must be a 2-D (T x K) matrix")
    if mat.shape[1] == 0:
        return 0.0
    # Drop degenerate (constant) columns.
    col_var = np.nanvar(mat, axis=0)
    mat = mat[:, col_var > _ZERO_VAR_EPS]
    if mat.shape[1] <= 1:
        return float(mat.shape[1])
    # Drop rows with any NaN so corrcoef is well-defined.
    mat = mat[~np.isnan(mat).any(axis=1)]
    if mat.shape[0] < 2:
        # Too few common-date observations to MEASURE correlation. Fail-closed:
        # "unmeasurable" must NOT pass as full independence (theses trading on
        # disjoint dates would otherwise be declared independent — the exact
        # correlated-sleeve false-positive the gate exists to catch).
        return 0.0
    cm = np.corrcoef(mat, rowvar=False)
    return effective_rank(cm)


def returns_matrix_from_records(
    records: Iterable[tuple[str, object, float]],
    *,
    fill: float = float("nan"),
) -> tuple[list[str], list, np.ndarray]:
    """Pivot (thesis_name, date, signed_return) records into a (T dates x K
    theses) matrix aligned by date — the adapter from the hindcast trade stream
    (e.g. replay KrHindcastTrade.thesis / entry_day / signed_return) to the
    effective-rank gate.

    Multiple records for the same (name, date) are averaged. Missing (name, date)
    cells default to NaN, which effective_rank_from_returns drops row-wise — i.e.
    theses are correlated on their common-date intersection, never on fabricated
    zeros. Names and dates are returned sorted for determinism.
    """
    sums: dict[tuple[str, object], float] = {}
    counts: dict[tuple[str, object], int] = {}
    name_set: set[str] = set()
    date_set: set = set()
    for name, day, r in records:
        key = (name, day)
        sums[key] = sums.get(key, 0.0) + float(r)
        counts[key] = counts.get(key, 0) + 1
        name_set.add(name)
        date_set.add(day)

    names = sorted(name_set)
    dates = sorted(date_set)
    matrix = np.full((len(dates), len(names)), fill, dtype=float)
    name_idx = {n: i for i, n in enumerate(names)}
    date_idx = {d: i for i, d in enumerate(dates)}
    for (name, day), total in sums.items():
        matrix[date_idx[day], name_idx[name]] = total / counts[(name, day)]
    return names, dates, matrix


@dataclass(frozen=True)
class ResumeVerdict:
    """Outcome of the two-leg resume gate (count AND independence)."""

    eligible: bool
    n_significant: int
    effective_rank: float
    reason: str


def resume_gate(
    p_values: Sequence[float],
    returns: np.ndarray,
    *,
    alpha: float = 0.05,
    min_significant: int = 3,
    min_eff_rank: float = 2.5,
) -> ResumeVerdict:
    """Two-leg resume gate: count of significant theses AND their independence.

    ``p_values`` (length K) and ``returns`` (T x K) must be column-aligned.
    Eligibility requires:
      1. at least ``min_significant`` theses with p < ``alpha``, AND
      2. those significant theses span at least ``min_eff_rank`` effective
         dimensions (participation ratio of their return correlation matrix).

    Counting correlated bets as independent (the 5/6-noise / rho=0.82 failure)
    fails leg 2 even when leg 1 passes.

    ``min_eff_rank`` defaults to 2.5, not the nominal 3.0: the effective rank of
    three genuinely independent finite-sample series sits just below 3 (O(1/sqrt(T))
    correlation noise pulls it to ~2.95-2.98), so a strict 3.0 would reject true
    independence. 2.5 carries that sampling tolerance while still rejecting a
    correlated pair (rho=0.82 collapses three columns to ~2.2 effective dims).
    """
    mat = np.asarray(returns, dtype=float)
    if mat.ndim != 2:
        raise ValueError("returns must be a 2-D (T x K) matrix")
    if mat.shape[1] != len(p_values):
        raise ValueError(
            f"p_values (len {len(p_values)}) must align with returns columns (K={mat.shape[1]})"
        )

    sig_idx = [i for i, p in enumerate(p_values) if p < alpha]
    n_sig = len(sig_idx)
    if n_sig < min_significant:
        return ResumeVerdict(
            eligible=False,
            n_significant=n_sig,
            effective_rank=float("nan"),
            reason=(f"only {n_sig} thesis(es) at p<{alpha}; need >= {min_significant}"),
        )

    er = effective_rank_from_returns(mat[:, sig_idx])
    eligible = er >= min_eff_rank
    if eligible:
        reason = (
            f"{n_sig} significant theses span {er:.2f} effective dimensions (>= {min_eff_rank})"
        )
    else:
        reason = (
            f"{n_sig} theses pass p<{alpha} but span only {er:.2f} "
            f"effective dimensions (< {min_eff_rank}) — correlated bets "
            f"counted as independent"
        )
    return ResumeVerdict(
        eligible=eligible,
        n_significant=n_sig,
        effective_rank=er,
        reason=reason,
    )
