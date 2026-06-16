"""Thesis return-series adapter — bridges hindcast trades to the resume gate.

The effective-rank resume gate (predictor/independence.py) needs a per-thesis,
date-aligned return matrix. The hindcast produces exactly that raw material —
KrHindcastTrade (thesis, entry_day, signed_return) accumulated per thesis — but
collapses it to scalars (auc/sharpe) before it reaches any gate. This module
persists/loads the trade stream and feeds it through the adapter so an operator
can compute the two-leg resume verdict (>=N significant AND >= effective-rank
independent) AFTER a hindcast run.

Decoupled and side-effect-free except for the explicit persist/load file I/O.
run_phase_kr_hindcast now exposes the raw trades on PhaseKrHindcastResult.
thesis_trades, so the end-to-end path is:

    result = await run_phase_kr_hindcast(...)
    verdict = resume_gate_from_records(
        trades_to_records(result.thesis_trades), p_values_by_thesis)

Persisting (persist_trades) is optional — for an operator to snapshot the trade
stream between runs. Computing/acting on the resume verdict stays operator-gated.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable, Mapping, Protocol, Sequence

from glostat.predictor.independence import (
    ResumeVerdict,
    resume_gate,
    returns_matrix_from_records,
)


class _Trade(Protocol):
    entry_day: object        # date (or anything with isoformat()/str())
    signed_return: float


Record = tuple[str, str, float]  # (thesis, date_iso, signed_return)


def trades_to_records(thesis_trades: Mapping[str, Iterable[_Trade]]) -> list[Record]:
    """Flatten {thesis -> trades} into (thesis, date_iso, signed_return) records."""
    records: list[Record] = []
    for thesis, trades in thesis_trades.items():
        for t in trades:
            iso = getattr(t.entry_day, "isoformat", None)
            day_iso = str(iso() if callable(iso) else t.entry_day)
            records.append((thesis, day_iso, float(t.signed_return)))
    return records


def persist_trades(thesis_trades: Mapping[str, Iterable[_Trade]], path: str | Path) -> int:
    """Write per-thesis trades to a JSONL artifact (additive; never touches the
    existing scalar reports). Returns the number of records written."""
    records = trades_to_records(thesis_trades)
    lines = [
        json.dumps({"thesis": n, "date": d, "signed_return": r}, ensure_ascii=False)
        for n, d, r in records
    ]
    out = Path(path)
    out.write_text("\n".join(lines) + ("\n" if lines else ""))
    return len(records)


def load_records(path: str | Path) -> list[Record]:
    """Read a trades JSONL artifact back into records. Malformed lines are skipped."""
    out: list[Record] = []
    for line in Path(path).read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
            out.append((str(obj["thesis"]), str(obj["date"]), float(obj["signed_return"])))
        except (json.JSONDecodeError, KeyError, TypeError, ValueError):
            continue
    return out


def resume_gate_from_records(
    records: Sequence[Record],
    p_values_by_thesis: Mapping[str, float],
    *,
    alpha: float = 0.05,
    min_significant: int = 3,
    min_eff_rank: float = 2.5,
) -> ResumeVerdict:
    """Two-leg resume verdict from the trade stream + per-thesis p-values.

    ``p_values_by_thesis`` maps thesis name -> AUC z-test p-value (e.g. from
    honesty.auc_p_value(overall_auc, n)). A thesis absent from the map is treated
    as not significant (p=1.0), fail-closed. Column order follows the adapter's
    sorted thesis names so p-values align with the return matrix."""
    names, _, matrix = returns_matrix_from_records(records)
    p_values = [float(p_values_by_thesis.get(name, 1.0)) for name in names]
    return resume_gate(
        p_values, matrix,
        alpha=alpha, min_significant=min_significant, min_eff_rank=min_eff_rank,
    )
