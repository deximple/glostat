"""v1.10.5 — re-run E_INSIDER_CLUSTER hindcast with relaxed gating.

Original spec (cluster_threshold=3, window_days=14) measured n=11 in the
2026-05-02 calibration table — below the 50-sample activation floor, so
calibration_status="underfit" and composite weight=0 despite IS Sharpe=+0.78.

This script re-runs the same hindcast with cluster_threshold=2 (still a
"cluster" by reasonable interpretation: ≥2 distinct insider buyers within
14d), aiming to grow n above 50 so the entry can be honestly labelled
measured (or measured-no-edge) rather than underfit.

Reads:
  - configs/universes/russell2k_top200_proxy.txt (60 small/mid-cap names)
  - $GLOSTAT_SEC_USER_AGENT (SEC EDGAR mandates real contact)

Writes:
  - cache/phase1b/e_insider_cluster_report.json (overwrites prior n=11
    measurement; calibration loader auto-picks up via _PHASE_SOURCES).

Usage:
  GLOSTAT_SEC_USER_AGENT="YourApp you@example.org" \\
    uv run python scripts/rerun_insider_cluster_hindcast.py [threshold] [window_days]

Defaults: threshold=2, window_days=14, start=2024-01-02, end=2026-03-29.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from dataclasses import asdict
from datetime import date
from pathlib import Path

import structlog

from glostat.data.sec_edgar_client import SecEdgarClient
from glostat.data.yfinance_client import YFinanceClient
from glostat.phase1b.cli import load_tickers, resolve_ciks
from glostat.phase1b.price_cache import PriceCache
from glostat.phase1b.runner_insider_cluster import run_insider_cluster_hindcast

log = structlog.get_logger(__name__)

_REPO_ROOT = Path(__file__).resolve().parents[1]
_RUSSELL_PATH = _REPO_ROOT / "configs" / "universes" / "russell2k_top200_proxy.txt"
_OUT_DIR = _REPO_ROOT / "cache" / "phase1b"


async def main(threshold: int, window_days: int) -> int:
    if "GLOSTAT_SEC_USER_AGENT" not in os.environ:
        print(
            "error: set GLOSTAT_SEC_USER_AGENT='YourApp you@example.org' "
            "before running (SEC mandates a real contact)",
            file=sys.stderr,
        )
        return 2

    start = date(2024, 1, 2)
    end = date(2026, 3, 29)

    yf = YFinanceClient()
    sec = SecEdgarClient()
    try:
        russell = load_tickers(_RUSSELL_PATH)
        log.info("rerun.tickers_loaded", n=len(russell))
        pairs = await resolve_ciks(russell, sec)
        log.info("rerun.ciks_resolved", n=len(pairs))

        cache = PriceCache(client=yf, start=start, end=end)
        report = await run_insider_cluster_hindcast(
            universe_with_cik=pairs, sec_client=sec, cache=cache,
            start=start, end=end,
            cluster_threshold=threshold, window_days=window_days,
        )
    finally:
        await sec.aclose()

    _OUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = _OUT_DIR / "e_insider_cluster_report.json"
    payload = {
        "report": _to_dict(report),
        "rerun_meta": {
            "cluster_threshold": threshold,
            "window_days": window_days,
            "rerun_reason": (
                "v1.10.5 relaxed gating to grow n above 50 floor; "
                "original spec was threshold=3, window=14d"
            ),
        },
    }
    out_path.write_text(json.dumps(payload, indent=2, default=str))

    print("=" * 70)
    print(f"E_INSIDER_CLUSTER re-hindcast (threshold={threshold}, window={window_days}d)")
    print("=" * 70)
    print(f"  n_signals     : {report.n_signals}")
    print(f"  AUC overall   : {report.overall_auc:.4f}")
    print(f"  AUC IS / OOS  : {report.is_auc:.4f} / {report.oos_auc:.4f}")
    print(f"  Sharpe overall: {report.overall_sharpe:+.4f}")
    print(f"  Sharpe IS/OOS : {report.is_sharpe:+.4f} / {report.oos_sharpe:+.4f}")
    print(f"  cost_passed_pct: {report.cost_passed_pct:.2%}")
    print(f"  → {out_path}")
    return 0


def _to_dict(report) -> dict:
    d = asdict(report)
    # Trim verbose row arrays for the persisted JSON (calibration only needs
    # the aggregate numbers, not per-trade detail).
    d["rows"] = [
        {**asdict(r), "day": r.day.isoformat()} for r in report.rows[:200]
    ]
    d["sample_dates"] = [dt.isoformat() for dt in report.sample_dates]
    if report.timestamp is not None:
        d["timestamp"] = report.timestamp.isoformat()
    d["oos_degradation"] = report.oos_degradation
    return d


if __name__ == "__main__":
    threshold_arg = int(sys.argv[1]) if len(sys.argv) > 1 else 2
    window_arg = int(sys.argv[2]) if len(sys.argv) > 2 else 14
    raise SystemExit(asyncio.run(main(threshold_arg, window_arg)))
