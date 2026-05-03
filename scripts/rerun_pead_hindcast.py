"""v1.10.12 — re-run E_PEAD hindcast for OOS_deg re-investigation.

The synthetic E_PEAD calibration (auc=0.586, sharpe=0.629, n=298,
oos_deg=1.156) ships hardcoded in synthetic_calibration_for_mock from
the original v0.6 phase1b run. Local cache/phase1b/e_pead_report.json
does not exist — we have no way to inspect WHY OOS_deg = 1.156 without
re-running the hindcast.

This script runs JUST the PEAD runner (not the full phase1b orchestrator
which would also re-run E_INSIDER_CLUSTER and overwrite the v1.10.5
measurement we want to keep). Writes phase1b/e_pead_report.json so the
calibration loader picks it up.

Usage:
  GLOSTAT_SEC_USER_AGENT="Your contact" \\
    uv run python scripts/rerun_pead_hindcast.py [start] [end]
"""
from __future__ import annotations

import asyncio
import json
import sys
from dataclasses import asdict
from datetime import date
from pathlib import Path

import structlog

from glostat.data.yfinance_client import YFinanceClient
from glostat.phase1b.cli import load_tickers
from glostat.phase1b.price_cache import PriceCache
from glostat.phase1b.runner_pead import run_pead_hindcast

log = structlog.get_logger(__name__)

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SP500_PATH = _REPO_ROOT / "configs" / "universes" / "sp500_top50.txt"
_OUT_DIR = _REPO_ROOT / "cache" / "phase1b"


async def main(start: date, end: date) -> int:
    yf = YFinanceClient()
    sp500 = load_tickers(_SP500_PATH)
    log.info("rerun_pead.tickers", n=len(sp500))
    cache = PriceCache(client=yf, start=start, end=end)
    report = await run_pead_hindcast(
        universe=sp500, yf_client=yf, cache=cache,
        start=start, end=end,
    )

    _OUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = _OUT_DIR / "e_pead_report.json"
    payload = {
        "report": _to_dict(report),
        "rerun_meta": {
            "rerun_reason": (
                "v1.10.12 OOS_deg re-investigation — synthetic baseline "
                "auc=0.586 sharpe=0.629 n=298 oos_deg=1.156 from v0.6 "
                "phase1b; live re-run to verify or update"
            ),
            "start": start.isoformat(),
            "end": end.isoformat(),
            "universe": "sp500_top50",
        },
    }
    out_path.write_text(json.dumps(payload, indent=2, default=str))

    print("=" * 70)
    print("E_PEAD re-hindcast complete (universe=SP500 top50)")
    print("=" * 70)
    print(f"  n_signals     : {report.n_signals}")
    print(f"  AUC overall   : {report.overall_auc:.4f}")
    print(f"  AUC IS / OOS  : {report.is_auc:.4f} / {report.oos_auc:.4f}")
    print(f"  Sharpe overall: {report.overall_sharpe:+.4f}")
    print(f"  Sharpe IS/OOS : {report.is_sharpe:+.4f} / {report.oos_sharpe:+.4f}")
    print(f"  OOS_deg       : {report.oos_degradation:.4f}")
    print(f"  cost_passed   : {report.cost_passed_pct:.2%}")
    print(f"  → {out_path}")
    return 0


def _to_dict(report) -> dict:
    d = asdict(report)
    d["rows"] = [
        {**asdict(r), "day": r.day.isoformat()} for r in report.rows[:200]
    ]
    d["sample_dates"] = [dt.isoformat() for dt in report.sample_dates]
    if report.timestamp is not None:
        d["timestamp"] = report.timestamp.isoformat()
    d["oos_degradation"] = report.oos_degradation
    return d


if __name__ == "__main__":
    s = date.fromisoformat(sys.argv[1]) if len(sys.argv) > 1 else date(2024, 1, 2)
    e = date.fromisoformat(sys.argv[2]) if len(sys.argv) > 2 else date(2026, 3, 29)
    raise SystemExit(asyncio.run(main(s, e)))
