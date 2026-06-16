"""Tests for glostat.replay.thesis_returns — hindcast-trades → resume-gate adapter."""

from __future__ import annotations

from datetime import date
from types import SimpleNamespace

from glostat.replay.thesis_returns import (
    load_records,
    persist_trades,
    resume_gate_from_records,
    trades_to_records,
)

_DATES = ["d0", "d1", "d2", "d3", "d4", "d5"]
# Three distinct return patterns over the same 6 dates (A,B near-orthogonal).
_PAT = {
    "E_A": [1.0, -1.0, 1.0, -1.0, 1.0, -1.0],
    "E_B": [1.0, 1.0, -1.0, -1.0, 1.0, 1.0],
    "E_C": [1.0, 1.0, 1.0, -1.0, -1.0, -1.0],
}


def _records(patterns):
    return [(th, _DATES[i], v) for th, seq in patterns.items() for i, v in enumerate(seq)]


# ── converter + persist/load ─────────────────────────────────────────────────


def test_trades_to_records_isoformats_dates():
    trades = {
        "E_X": [SimpleNamespace(entry_day=date(2026, 1, 1), signed_return=2.0)],
        "E_Y": [SimpleNamespace(entry_day=date(2026, 1, 2), signed_return=-1.0)],
    }
    recs = sorted(trades_to_records(trades))
    assert recs == [("E_X", "2026-01-01", 2.0), ("E_Y", "2026-01-02", -1.0)]


def test_persist_load_roundtrip(tmp_path):
    trades = {
        "E_X": [
            SimpleNamespace(entry_day=date(2026, 1, 1), signed_return=2.0),
            SimpleNamespace(entry_day=date(2026, 1, 2), signed_return=3.0),
        ],
    }
    p = tmp_path / "trades.jsonl"
    n = persist_trades(trades, p)
    assert n == 2
    assert sorted(load_records(p)) == [
        ("E_X", "2026-01-01", 2.0),
        ("E_X", "2026-01-02", 3.0),
    ]


def test_load_skips_malformed_lines(tmp_path):
    p = tmp_path / "t.jsonl"
    p.write_text('{"thesis":"A","date":"d0","signed_return":1.0}\nGARBAGE\n{}\n')
    assert load_records(p) == [("A", "d0", 1.0)]


# ── resume gate from records ─────────────────────────────────────────────────


def test_resume_eligible_when_three_independent_significant():
    recs = _records(_PAT)
    p = {"E_A": 0.01, "E_B": 0.02, "E_C": 0.03}
    v = resume_gate_from_records(recs, p)
    assert v.n_significant == 3
    assert v.effective_rank >= 2.5
    assert v.eligible is True


def test_resume_blocked_when_two_are_collinear():
    pats = {"E_A": _PAT["E_A"], "E_B": _PAT["E_A"], "E_C": _PAT["E_C"]}  # A==B
    v = resume_gate_from_records(_records(pats), {"E_A": 0.01, "E_B": 0.01, "E_C": 0.02})
    assert v.n_significant == 3
    assert v.effective_rank < 2.5
    assert v.eligible is False


def test_resume_blocked_when_too_few_significant():
    recs = _records(_PAT)
    p = {"E_A": 0.01, "E_B": 0.40, "E_C": 0.30}  # only one significant
    v = resume_gate_from_records(recs, p)
    assert v.n_significant == 1
    assert v.eligible is False


def test_resume_missing_pvalue_is_not_significant():
    recs = _records(_PAT)
    v = resume_gate_from_records(recs, {"E_A": 0.01})  # B, C absent → p=1.0
    assert v.n_significant == 1
    assert v.eligible is False
