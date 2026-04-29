#!/usr/bin/env python3
"""SK이노베이션 (096770) — v1.1 vs v1.2 prediction comparison.

Captures the v1.1 baseline (synthetic calibration, no DART, no phase_kr report)
against the v1.2 calibration (with phase_kr cache present + DART overlay if
GLOSTAT_DART_API_KEY is set). Writes a side-by-side markdown report so the
user can verify that the L1 calibration update + L2 DART overlay actually
move the prediction.

Usage:
  uv run python scripts/compare_sk_innovation.py
  GLOSTAT_DART_API_KEY=... uv run python scripts/compare_sk_innovation.py

Honest reporting: if DART is not configured, the report says so and the L2
column is omitted.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

# Make the in-tree package importable when running as a script
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from glostat.cli_predict_print import print_prediction
from glostat.data.dart_client import is_dart_configured
from glostat.data.data_router import DataRouter, is_kr_ticker
from glostat.data.naver_kr_client import NaverKrClient
from glostat.data.sec_edgar_client import SecEdgarClient
from glostat.data.snapshot_broker import SnapshotBroker
from glostat.data.yfinance_client import YFinanceClient
from glostat.experts import (
    EForeignReversalExpert,
    EFundamentalExpert,
    EFundamentalKrExpert,
    EFundFlowExpert,
    EInsiderKrExpert,
    ETimeExpert,
)
from glostat.predictor.calibration import (
    CalibrationTable,
    load_calibration,
    synthetic_calibration_for_mock,
)
from glostat.predictor.composite import predict
from glostat.predictor.kr_universe import KOSPI200_UNIVERSE
from glostat.predictor.thesis_wrappers import collect_contributions
from glostat.predictor.types import Prediction

_TICKER = "096770"
_OUTPUT = Path("cache") / "comparisons" / "sk_innovation_v1.1_vs_v1.2.md"


async def _run(*, cal_table: CalibrationTable, with_dart: bool) -> Prediction:
    ts = datetime.now(tz=UTC)
    broker = SnapshotBroker(root=Path("cache") / "snapshots")
    yf = YFinanceClient(snapshot_broker=broker)
    sec_user_agent = os.environ.get(
        "GLOSTAT_SEC_USER_AGENT", "GLOSTAT (deximple@gmail.com)",
    )
    sec = SecEdgarClient(user_agent=sec_user_agent, snapshot_broker=broker)
    naver = NaverKrClient()
    router = DataRouter()
    router.register_client("yfinance", yf)
    router.register_client("sec_edgar", sec)
    router.register_client("naver_kr", naver)
    fundamental = EFundamentalExpert(router=router)
    time_expert = ETimeExpert(router=router)
    fund_flow = EFundFlowExpert(router=router)
    fundamental_kr = EFundamentalKrExpert(router=router, enable_dart=with_dart)
    foreign_reversal = EForeignReversalExpert(
        router=router, kospi200=KOSPI200_UNIVERSE,
    )
    insider_kr = (
        EInsiderKrExpert.from_env(kospi200=KOSPI200_UNIVERSE) if with_dart else None
    )
    try:
        contribs = await collect_contributions(
            ticker=_TICKER, ts=ts, cal_table=cal_table,
            fundamental_expert=fundamental,
            time_expert=time_expert,
            fund_flow_expert=fund_flow,
            fundamental_kr_expert=fundamental_kr,
            foreign_reversal_expert=foreign_reversal,
            insider_kr_expert=insider_kr,
        )
    finally:
        await sec.aclose()
        if insider_kr is not None and insider_kr._dart is not None:
            try:
                await insider_kr._dart.aclose()
            except Exception:
                pass
        broker.close()
    market = "XKRX" if is_kr_ticker(_TICKER) else "XNAS"
    return predict(
        ticker=_TICKER, horizon="swing_30d", contributions=contribs,
        cal_table=cal_table, issued_at=ts, market=market,
    )


def _fmt_signal(p: Prediction) -> str:
    out: list[str] = [
        f"  up/down/sideways: {p.up_probability * 100:.1f}% / "
        f"{p.down_probability * 100:.1f}% / "
        f"{p.sideways_probability * 100:.1f}%",
        f"  expected_return:  {p.expected_return_bps:+.0f}bps "
        f"(CI: {p.confidence_interval_bps[0]:+.0f}..{p.confidence_interval_bps[1]:+.0f})",
        f"  base_rate_up:     {p.base_rate_up * 100:.1f}%",
        f"  edge_over_base:   {p.edge_over_baseline_pp:+.2f}pp",
        "",
        "  Active signals:",
    ]
    for c in p.contributing_signals:
        if c.direction == "skip":
            continue
        out.append(
            f"    {c.name:<22} {c.direction:>4} value={c.value:+.3f} "
            f"AUC={c.calibration_auc:.3f} n={c.n_samples}"
        )
    out.append("")
    out.append("  Skipped signals:")
    for c in p.contributing_signals:
        if c.direction != "skip":
            continue
        out.append(f"    {c.name:<22} skip ({c.skip_reason})")
    return "\n".join(out)


def _render_comparison(
    *, v11: Prediction, v12: Prediction, v12_with_dart: Prediction | None,
    cal_v11: CalibrationTable, cal_v12: CalibrationTable,
) -> str:
    delta_p = (v12.up_probability - v11.up_probability) * 100
    delta_e = v12.edge_over_baseline_pp - v11.edge_over_baseline_pp

    lines: list[str] = [
        "# SK이노베이션 (096770) — v1.1 vs v1.2 Prediction Comparison",
        "",
        f"Generated: {datetime.now(tz=UTC).isoformat()}",
        "",
        "## TL;DR",
        "",
        "| metric | v1.1 (synthetic baseline) | v1.2 (phase_kr calibration) | Δ |",
        "|---|---:|---:|---:|",
        f"| p_up | {v11.up_probability * 100:.2f}% | "
        f"{v12.up_probability * 100:.2f}% | {delta_p:+.2f}pp |",
        f"| edge_over_baseline | {v11.edge_over_baseline_pp:+.2f}pp | "
        f"{v12.edge_over_baseline_pp:+.2f}pp | {delta_e:+.2f}pp |",
        f"| active signals | {v11.active_signal_count} | "
        f"{v12.active_signal_count} | {v12.active_signal_count - v11.active_signal_count:+d} |",
        f"| total slots | {v11.total_signal_count} | "
        f"{v12.total_signal_count} | – |",
        "",
        "## v1.1 — Synthetic baseline (no phase_kr cache, no DART)",
        "",
        "```",
        _fmt_signal(v11),
        "```",
        "",
        "## v1.2 — phase_kr-calibrated (DART unused)",
        "",
        "```",
        _fmt_signal(v12),
        "```",
        "",
    ]
    if v12_with_dart is not None:
        lines.extend([
            "## v1.2 — phase_kr-calibrated + DART overlay",
            "",
            "```",
            _fmt_signal(v12_with_dart),
            "```",
            "",
        ])
    else:
        lines.extend([
            "## v1.2 + DART overlay",
            "",
            "DART API key not configured — column omitted. Set "
            "`GLOSTAT_DART_API_KEY` (free signup at https://opendart.fss.or.kr/) "
            "to populate this column. See `docs/DART_API_SETUP.md`.",
            "",
        ])

    lines.extend([
        "## Calibration table delta (KR-active theses)",
        "",
        "| thesis | v1.1 AUC | v1.1 n | v1.2 AUC | v1.2 n |",
        "|---|---:|---:|---:|---:|",
    ])
    for name in ("E_FUNDAMENTAL_KR", "E_TIME_KR", "E_FOREIGN_REVERSAL", "E_INSIDER_KR"):
        a = cal_v11.entries.get(name)
        b = cal_v12.entries.get(name)
        a_auc = f"{a.auc:.4f}" if a else "n/a"
        a_n = str(a.n_samples) if a else "n/a"
        b_auc = f"{b.auc:.4f}" if b else "n/a"
        b_n = str(b.n_samples) if b else "n/a"
        lines.append(f"| {name} | {a_auc} | {a_n} | {b_auc} | {b_n} |")
    lines.append("")
    lines.append("Generated by `scripts/compare_sk_innovation.py`. Personal use only.")
    return "\n".join(lines)


async def _main_async() -> int:
    cal_v11 = synthetic_calibration_for_mock()
    cal_v12 = load_calibration()

    print(">>> Running v1.1 baseline (synthetic calibration)...")
    v11 = await _run(cal_table=cal_v11, with_dart=False)
    print_prediction(v11)

    print("\n>>> Running v1.2 (phase_kr calibration if cached)...")
    v12 = await _run(cal_table=cal_v12, with_dart=False)
    print_prediction(v12)

    v12_dart: Prediction | None = None
    if is_dart_configured():
        print("\n>>> Running v1.2 + DART overlay...")
        v12_dart = await _run(cal_table=cal_v12, with_dart=True)
        print_prediction(v12_dart)
    else:
        print("\n>>> DART not configured — skipping L2 overlay run.")

    body = _render_comparison(
        v11=v11, v12=v12, v12_with_dart=v12_dart,
        cal_v11=cal_v11, cal_v12=cal_v12,
    )
    _OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    _OUTPUT.write_text(body, encoding="utf-8")
    print(f"\n[saved → {_OUTPUT}]")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="SK이노베이션 v1.1 vs v1.2 prediction comparison.",
    )
    parser.parse_args()
    return asyncio.run(_main_async())


if __name__ == "__main__":
    raise SystemExit(main())
