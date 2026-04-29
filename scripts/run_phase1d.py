#!/usr/bin/env python3
"""Phase 1D — empirical hindcast of 2 orthogonal-asset theses.

Runs:
  1. E7 Crypto Funding Carry (BTC, ETH on Binance perp futures)
  2. E9 KR 외인 Reversal + 기관 Dual Flow (KOSPI 200 sample)

Writes per-thesis reports + a side-by-side comparison to
cache/hindcast/phase1d/.
"""
from __future__ import annotations

import asyncio
import logging
import sys
from datetime import date
from pathlib import Path

# Make the in-tree package importable when running as a script
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from glostat.replay.phase1d_hindcast import (
    hindcast_foreign_reversal,
    hindcast_funding_carry,
    persist_phase1d_report,
    render_gate_summary,
    render_report_md,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
    stream=sys.stdout,
)


# KOSPI 200 sample — top liquidity tickers (subset for runtime).
_KOSPI200_SAMPLE: tuple[str, ...] = (
    "005930",  # Samsung Electronics
    "000660",  # SK Hynix
    "035720",  # Kakao
    "035420",  # Naver
    "005380",  # Hyundai Motor
    "000270",  # Kia
    "207940",  # Samsung Biologics
    "005490",  # POSCO Holdings
    "068270",  # Celltrion
    "066570",  # LG Electronics
    "017670",  # SK Telecom
    "030200",  # KT
    "051910",  # LG Chem
    "006400",  # Samsung SDI
    "012330",  # Hyundai Mobis
    "028260",  # Samsung C&T
    "055550",  # Shinhan Financial
    "086790",  # Hana Financial
    "105560",  # KB Financial
    "316140",  # Woori Financial
)


async def main_async() -> int:
    out_dir = Path("cache/hindcast/phase1d")
    out_dir.mkdir(parents=True, exist_ok=True)

    # -- Thesis E7 Crypto Funding Carry ----------------------------------
    print("\n=== Phase 1D Thesis E7: Crypto Funding Carry ===\n")
    e7_report = await hindcast_funding_carry(
        symbols=("BTC/USDT:USDT", "ETH/USDT:USDT"),
        start=date(2024, 1, 1),
        end=date(2026, 3, 31),
        split_ratio=0.7,
    )
    e7_path = persist_phase1d_report(e7_report, output_dir=out_dir)
    print(render_report_md(e7_report))
    print()
    print(render_gate_summary(e7_report))
    print(f"\n[saved → {e7_path}]\n")

    # -- Thesis E9 KR 외인 Reversal --------------------------------------
    print("\n=== Phase 1D Thesis E9: KR 외인 Reversal (TITAN B4) ===\n")
    e9_report = await hindcast_foreign_reversal(
        codes=_KOSPI200_SAMPLE,
        start=date(2024, 1, 1),
        end=date(2026, 3, 31),
        split_ratio=0.7,
        max_pages=30,
    )
    e9_path = persist_phase1d_report(e9_report, output_dir=out_dir)
    print(render_report_md(e9_report))
    print()
    print(render_gate_summary(e9_report))
    print(f"\n[saved → {e9_path}]\n")

    # -- Side-by-side comparison ----------------------------------------
    comparison = _comparison_md(e7_report, e9_report)
    cmp_path = out_dir / "phase1d_comparison.md"
    cmp_path.write_text(comparison, encoding="utf-8")
    print("\n=== COMPARISON ===\n")
    print(comparison)
    print(f"\n[saved → {cmp_path}]\n")
    return 0


def _comparison_md(e7, e9) -> str:
    lines: list[str] = []
    lines.append("# Phase 1D — Orthogonal Thesis Comparison")
    lines.append("")
    lines.append("Side-by-side empirical hindcast (live data, no Bigdata MCP).")
    lines.append("")
    lines.append("| metric | E7 Funding Carry | E9 KR 외인 Reversal |")
    lines.append("|---|---:|---:|")
    lines.append(f"| universe size | {len(e7.universe)} | {len(e9.universe)} |")
    lines.append(f"| bars evaluated | {e7.n_bars_evaluated:,} | {e9.n_bars_evaluated:,} |")
    lines.append(
        f"| INSUFFICIENT skip | {e7.n_skip_insufficient:,} | "
        f"{e9.n_skip_insufficient:,} |"
    )
    lines.append(f"| actionable (pre-cost) | {e7.n_actionable:,} | {e9.n_actionable:,} |")
    lines.append(f"| traded (post-cost) | {e7.n_traded:,} | {e9.n_traded:,} |")
    lines.append(
        f"| cost_passed_pct | {e7.cost_passed_pct:.1%} | {e9.cost_passed_pct:.1%} |"
    )
    lines.append(
        f"| **Sharpe (overall)** | {e7.overall_sharpe:.4f} | {e9.overall_sharpe:.4f} |"
    )
    lines.append(f"| Sharpe IS | {e7.is_sharpe:.4f} | {e9.is_sharpe:.4f} |")
    lines.append(f"| Sharpe OOS | {e7.oos_sharpe:.4f} | {e9.oos_sharpe:.4f} |")
    lines.append(f"| OOS degradation | {e7.oos_degradation:.2%} | {e9.oos_degradation:.2%} |")
    lines.append(f"| **AUC (overall)** | {e7.overall_auc:.4f} | {e9.overall_auc:.4f} |")
    lines.append(f"| AUC IS | {e7.is_auc:.4f} | {e9.is_auc:.4f} |")
    lines.append(f"| AUC OOS | {e7.oos_auc:.4f} | {e9.oos_auc:.4f} |")
    lines.append(f"| MaxDD | {e7.overall_maxdd:.2%} | {e9.overall_maxdd:.2%} |")
    lines.append(f"| hit_rate_actionable | {e7.hit_rate_actionable:.2%} | {e9.hit_rate_actionable:.2%} |")
    lines.append(
        f"| avg_actionable_return | {e7.avg_actionable_return:+.4%} | "
        f"{e9.avg_actionable_return:+.4%} |"
    )
    lines.append("")
    lines.append("## Notes")
    lines.append("")
    for n in e7.notes:
        lines.append(f"- E7: {n}")
    for n in e9.notes:
        lines.append(f"- E9: {n}")
    lines.append("")
    lines.append("## TITAN B4 Confirmation Test")
    lines.append("")
    e9_b4_hr = e9.pattern_hit_rates.get("REVERSAL_BUY")
    if e9_b4_hr is None:
        lines.append("- E9 produced no REVERSAL_BUY post-cost-gate trades; cannot confirm 60.3%.")
    else:
        delta = (e9_b4_hr - 0.603) * 100
        lines.append(
            "- TITAN B4 historical: 60.3% (58 events, KR universe, 2025.06–2026.03)"
        )
        lines.append(
            f"- Phase 1D live hindcast: **{e9_b4_hr:.1%}** "
            f"({e9.pattern_breakdown.get('REVERSAL_BUY', 0)} events, "
            f"2024.01–2026.03)"
        )
        lines.append(f"- Δ vs TITAN: {delta:+.1f} pp")
        if abs(delta) < 5.0:
            lines.append("- **CONFIRMED** (within ±5pp tolerance)")
        elif delta > 0:
            lines.append(f"- **EXCEEDED** TITAN baseline by {delta:.1f}pp")
        else:
            lines.append(
                f"- **UNDERPERFORMED** TITAN baseline by {abs(delta):.1f}pp — "
                "TITAN's 60.3% may not generalize to longer history / wider universe"
            )
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    return asyncio.run(main_async())


if __name__ == "__main__":
    raise SystemExit(main())
