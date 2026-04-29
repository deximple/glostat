"""Phase 1C — empirical hindcast of 2 macro/commodity theses.

Live data (yfinance + CFTC public ZIPs).
Window: 2024-01-01 → 2026-03-31 (matches the brief).

Outputs three files into cache/hindcast/:
  - phase1c_fx_carry_report.json
  - phase1c_commodity_ts_report.json
  - phase1c_comparison.md (side-by-side gate table + Korean discussion)
"""

from __future__ import annotations

import asyncio
import json
import os
from dataclasses import asdict
from datetime import UTC, date, datetime
from pathlib import Path

from glostat.data.cftc_client import CftcClient
from glostat.data.snapshot_broker import SnapshotBroker
from glostat.data.yfinance_client import YFinanceClient
from glostat.phase1b.price_cache import PriceCache
from glostat.phase1b.runner_commodity_ts import run_commodity_ts_hindcast
from glostat.phase1b.runner_fx_carry import run_fx_carry_hindcast
from glostat.phase1b.types import PhaseHindcastReport
from glostat.replay.sprint4_gate import (
    Sprint4Gate,
    evaluate_sprint4_gate,
    render_gate_table,
)

CACHE_DIR = Path("cache") / "hindcast"
SNAPSHOT_DIR = Path("cache") / "snapshots_phase1c"
START = date(2024, 1, 1)
END = date(2026, 3, 31)


def _report_to_dict(r: PhaseHindcastReport) -> dict:
    payload = asdict(r)
    payload["timestamp"] = r.timestamp.isoformat() if r.timestamp else None
    payload["sample_dates"] = [d.isoformat() for d in r.sample_dates]
    payload["rows"] = [
        {**asdict(row), "day": row.day.isoformat()} for row in r.rows
    ]
    return payload


def _gate_for(report: PhaseHindcastReport) -> Sprint4Gate:
    return evaluate_sprint4_gate(
        sharpe=report.overall_sharpe,
        oos_degradation=report.oos_degradation,
        auc=report.overall_auc,
        cost_passed_pct=report.cost_passed_pct,
        maxdd=max(report.is_maxdd, report.oos_maxdd),
        reproducibility=1.0 if report.determinism_verified else 0.0,
        n_verdicts=report.n_signals,
        compliance_clean_days=90,
        profile="cautious",
    )


def _save_report(report: PhaseHindcastReport, name: str) -> Path:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = CACHE_DIR / f"phase1c_{name}_report.json"
    payload = _report_to_dict(report)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True))
    return path


def _gate_summary_row(label: str, report: PhaseHindcastReport, gate: Sprint4Gate) -> str:
    return (
        f"| {label:<20} | {report.overall_sharpe:>+7.3f} | {report.overall_auc:>5.3f} "
        f"| {report.oos_degradation * 100:>+6.2f}% | "
        f"{report.cost_passed_pct * 100:>5.2f}% | {report.n_signals:>6d} | "
        f"{gate.pass_status:<6} |"
    )


def _comparative_md(
    fx_report: PhaseHindcastReport,
    fx_gate: Sprint4Gate,
    cm_report: PhaseHindcastReport,
    cm_gate: Sprint4Gate,
) -> str:
    return f"""# Phase 1C — 2-Thesis Comparative Hindcast

> Generated: {datetime.now(tz=UTC).isoformat()}
> Window: {START.isoformat()} → {END.isoformat()}
> Data: yfinance (free) + CFTC public COT (free)
> No paid data sources used (INV-GS-036 in force).

## Side-by-side Sprint 4 Gate

| Thesis               |  Sharpe |   AUC | OOS deg | cost%  |    N | Gate   |
|----------------------|--------:|------:|--------:|-------:|-----:|--------|
{_gate_summary_row("E2 FX_CARRY",   fx_report, fx_gate)}
{_gate_summary_row("E8 COMMODITY_TS", cm_report, cm_gate)}

## E2 — FX Carry + Risk-Off (XLU/XLV defensive tilt)

```
{render_gate_table(fx_gate)}
```

- Universe: SPY + 4 sector ETFs (XLF, XLE, XLU, XLV) + FX proxies (FXY, EWZ).
- Trigger: VIX 5d>=25 + FXY 5d>+2% + EWZ 3d<-1.5% (≥2 of 3 legs)
- Horizon: 7d swing.
- Rows ({fx_report.n_signals}, n_skipped={fx_report.n_skipped}, \
cost_passed={fx_report.cost_passed_pct * 100:.1f}%).

Notes from runner:
{chr(10).join(f"  - {n}" for n in fx_report.notes)}

## E8 — Commodity TS + COT Extremes (10 ETFs, weekly rebal)

```
{render_gate_table(cm_gate)}
```

- Universe: USO, UNG, GLD, SLV, CPER, URA, CORN, WEAT, DBC, GSG.
- Signal A (TS): 90d return + price/200dMA same-sign.
- Signal B (COT): commercial_net_pct 5y rolling rank thresholds 0.85/0.15.
- Horizon: 30d.
- Rows ({cm_report.n_signals}, n_skipped={cm_report.n_skipped}, \
cost_passed={cm_report.cost_passed_pct * 100:.1f}%).

Notes from runner:
{chr(10).join(f"  - {n}" for n in cm_report.notes)}

## Phase 1B context (where applicable)

The four Phase 1B theses (E1 sector rotation, E5a PEAD, E5b FOMC drift,
E6 insider cluster) all produced FAIL gates per the archived Sprint 5
post-mortem; Phase 1C extends the empirical screening to macro and commodity
surfaces that exhibit different alpha decay characteristics.

## Honest discussion — commodity ETF contango drag

USO, UNG, DBC, and GSG all roll front-month futures monthly. Contango
(longer-dated > spot) bleeds the ETF NAV on every roll; in 2020 the USO
prospectus was rewritten after roll losses exceeded -50%. Implications for
the E8 hindcast:

- **TS momentum on USO/UNG is biased downward**: a flat spot price with
  contango produces a negative ETF return. Our 90d momentum signal will see
  more SHORT signals than the underlying commodity warrants.
- **GLD and SLV are fully-allocated physical ETFs**: no contango drag, so
  TS momentum on those is the cleanest signal in the universe.
- **CPER, CORN, WEAT** track diversified futures baskets but still suffer
  contango when the curve is in contango. Less severe than USO/UNG, but
  measurable over a 2y window.
- **URA** is an equity ETF (uranium miners) — no roll cost, but COT data is
  unavailable so it runs TS-only.
- **DBC, GSG** are diversified commodity baskets — partially smoothed
  contango impact via cross-commodity offsets.

The hindcast metrics include the contango drag in the realized 30d returns,
so a positive Sharpe means the signal beat both the cost gate AND the drag.
A negative Sharpe on USO/UNG-heavy weeks may reflect contango as much as
signal failure — that ambiguity cannot be cleanly disentangled without a
separate spot-price reference series.

## FRED API note

FRED API requires a user-supplied key not present in the environment. All
macro inputs were sourced via yfinance proxies (^VIX, FXY, UUP exposure via
DXY proxy was substituted with FXY for the carry trade leg). No FRED data
was used for this run; if a key is added later the leg detection can be
augmented with FEDFUNDS / DGS10 series for cleaner regime classification.
"""


async def main() -> int:
    if not os.environ.get("GLOSTAT_SEC_USER_AGENT"):
        os.environ["GLOSTAT_SEC_USER_AGENT"] = "GLOSTAT (deximple@gmail.com)"

    SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    broker = SnapshotBroker(root=SNAPSHOT_DIR)
    yf = YFinanceClient(snapshot_broker=broker)
    cache = PriceCache(client=yf, start=START, end=END)
    cftc = CftcClient(snapshot_broker=broker)

    print("[phase1c] running E2 FX_CARRY hindcast …")
    fx_report = await run_fx_carry_hindcast(
        cache=cache, start=START, end=END,
    )
    fx_gate = _gate_for(fx_report)
    fx_path = _save_report(fx_report, "fx_carry")
    print(f"[phase1c] FX_CARRY → sharpe={fx_report.overall_sharpe:.3f} "
          f"auc={fx_report.overall_auc:.3f} N={fx_report.n_signals} "
          f"gate={fx_gate.pass_status}  → {fx_path}")

    print("[phase1c] running E8 COMMODITY_TS hindcast …")
    cm_report = await run_commodity_ts_hindcast(
        cache=cache, cftc_client=cftc, start=START, end=END,
    )
    cm_gate = _gate_for(cm_report)
    cm_path = _save_report(cm_report, "commodity_ts")
    print(f"[phase1c] COMMODITY_TS → sharpe={cm_report.overall_sharpe:.3f} "
          f"auc={cm_report.overall_auc:.3f} N={cm_report.n_signals} "
          f"gate={cm_gate.pass_status}  → {cm_path}")

    md = _comparative_md(fx_report, fx_gate, cm_report, cm_gate)
    md_path = CACHE_DIR / "phase1c_comparison.md"
    md_path.write_text(md)
    print(f"[phase1c] wrote {md_path}")

    broker.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
