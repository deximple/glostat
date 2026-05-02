from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

from glostat.cli_predictor import _predict_live
from glostat.data.snapshot_broker import SnapshotBroker
from glostat.data.universe import load_universe
from glostat.predictor.calibration import load_calibration
from glostat.predictor.honesty import is_statistically_significant
from glostat.predictor.types import Prediction
from glostat.risk.compliance_gate import ComplianceContext, assert_personal_use

# v1.9.0 — `glostat scan` subcommand. Universe-wide ranking of predictions
# by composite edge over baseline, with optional filter for "at least one
# active signal carries statistically significant AUC (p<0.05)".
#
# WHY: existing `glostat predict <ticker>` is single-ticker. The Cross-Stock
# Acid Test (2026-05-02) revealed that 4 of 5 cyclical KR tickers landed at
# identical p_up = 53.4% before calibration. After calibration filling, the
# differentiation appeared (현대건설 +1.6pp LONG vs others NEAR-FLAT).
# The scan command surfaces this differentiation across an entire universe
# in a single call — the natural product layer for the calibrated framework.

_DEFAULT_SNAPSHOT_ROOT: Final[Path] = Path("cache") / "snapshots"
_DEFAULT_UNIVERSE: Final[str] = "KR_KOSPI200_TOP30"
_DEFAULT_TOP_N: Final[int] = 10
_DEFAULT_HORIZON: Final[str] = "swing_30d"


def add_scan_subparser(sub: Any) -> None:
    p = sub.add_parser(
        "scan",
        help="Rank a universe by composite edge over baseline.",
    )
    p.add_argument("--universe", default=_DEFAULT_UNIVERSE,
                   help=f"Universe name (default {_DEFAULT_UNIVERSE}).")
    p.add_argument("--top", type=int, default=_DEFAULT_TOP_N,
                   help=f"Show top N tickers. Default {_DEFAULT_TOP_N}.")
    p.add_argument("--horizon", default=_DEFAULT_HORIZON,
                   choices=["intraday", "swing_5d", "swing_30d", "long_3y"],
                   help="Prediction horizon. Default swing_30d.")
    p.add_argument("--significant", action="store_true",
                   help="Filter to tickers where at least one active signal "
                        "has p<0.05 (statistically significant AUC).")
    p.add_argument("--min-edge", type=float, default=None,
                   help="Filter to tickers with abs(edge_over_baseline_pp) >= this.")
    p.add_argument("--max-concurrent", type=int, default=3,
                   help="Parallel ticker semaphore. Default 3 (yfinance throttle).")
    p.add_argument("--jurisdiction", default="US",
                   choices=["KR", "US", "EU", "JP", "TW", "HK", "DEFAULT"],
                   help="Compliance disclaimer jurisdiction. Default US.")


def cmd_scan(args: argparse.Namespace) -> int:
    universe = load_universe(args.universe)
    if not universe.tickers:
        print(f"[scan] universe {args.universe} has no tickers", file=sys.stderr)
        return 2

    ctx = ComplianceContext(
        user_profile_hash="0" * 64, jurisdiction=args.jurisdiction,
    )
    assert_personal_use(ctx)

    broker = SnapshotBroker(root=_DEFAULT_SNAPSHOT_ROOT)
    cal_table = load_calibration()

    print(f"=== GLOSTAT Scan — {args.universe} ({datetime.now(tz=UTC).date()}) ===")
    print(
        f"  horizon={args.horizon}  tickers={len(universe.tickers)}  "
        f"max_concurrent={args.max_concurrent}"
    )
    if args.significant:
        print("  filter: only show tickers with at least one signal at p<0.05")
    if args.min_edge is not None:
        print(f"  filter: |edge| >= {args.min_edge}pp")
    print()

    try:
        results = asyncio.run(_scan_universe(
            tickers=universe.tickers, horizon=args.horizon,
            broker=broker, cal_table=cal_table,
            max_concurrent=args.max_concurrent,
        ))
    finally:
        broker.close()

    filtered = _apply_filters(
        results, significant=args.significant, min_edge=args.min_edge,
    )
    if not filtered:
        print(
            f"[scan] no tickers passed filters "
            f"({len(results)} predictions, 0 surviving)"
        )
        return 0

    ranked = sorted(
        filtered, key=lambda r: r[1].edge_over_baseline_pp, reverse=True,
    )
    _print_scan_table(ranked[: args.top])
    print()
    print(
        "Personal use only. Not investment advice. "
        "Calibration historical; future not guaranteed (INV-GS-024)."
    )
    return 0


async def _scan_universe(
    *,
    tickers: tuple[str, ...],
    horizon: str,
    broker: SnapshotBroker,
    cal_table: Any,
    max_concurrent: int,
) -> list[tuple[str, Prediction]]:
    semaphore = asyncio.Semaphore(max(1, max_concurrent))
    results: list[tuple[str, Prediction]] = []

    async def predict_one(ticker: str) -> None:
        async with semaphore:
            try:
                pred = await _predict_live(
                    ticker=ticker, horizon=horizon,  # type: ignore[arg-type]
                    ts=datetime.now(tz=UTC),
                    broker=broker, cal_table=cal_table,
                )
                results.append((ticker, pred))
            except Exception as exc:
                print(
                    f"[scan] {ticker} skipped: {exc}",
                    file=sys.stderr,
                )

    await asyncio.gather(*(predict_one(t) for t in tickers))
    return results


def _apply_filters(
    results: list[tuple[str, Prediction]],
    *,
    significant: bool,
    min_edge: float | None,
) -> list[tuple[str, Prediction]]:
    out: list[tuple[str, Prediction]] = []
    for ticker, pred in results:
        if min_edge is not None and abs(pred.edge_over_baseline_pp) < min_edge:
            continue
        if significant and not _has_significant_signal(pred):
            continue
        out.append((ticker, pred))
    return out


def _has_significant_signal(pred: Prediction) -> bool:
    for sig in pred.contributing_signals:
        if sig.direction == "skip" or sig.n_samples == 0:
            continue
        if is_statistically_significant(sig.calibration_auc, sig.n_samples):
            return True
    return False


def _print_scan_table(ranked: list[tuple[str, Prediction]]) -> None:
    print(
        f"  {'RANK':>4}  {'TICKER':<8}  {'p_up':>5}  {'edge':>6}  "
        f"{'net_bps':>8}  top_signal"
    )
    print("  " + "-" * 80)
    for i, (ticker, pred) in enumerate(ranked, 1):
        net_bps = pred.expected_return_bps  # already net of expected return
        top = _top_active_signal(pred)
        line = (
            f"  {i:>4}  {ticker:<8}  "
            f"{pred.up_probability * 100:>4.1f}%  "
            f"{pred.edge_over_baseline_pp:>+5.1f}pp  "
            f"{net_bps:>+7.0f}  {top}"
        )
        print(line)


def _top_active_signal(pred: Prediction) -> str:
    # Pick the active signal with the largest |value| as the headline.
    best: tuple[float, str] | None = None
    for sig in pred.contributing_signals:
        if sig.direction == "skip" or sig.value is None or sig.n_samples == 0:
            continue
        score = abs(sig.value)
        sig_marker = (
            "p<0.05" if is_statistically_significant(
                sig.calibration_auc, sig.n_samples,
            ) else "n.s."
        )
        label = (
            f"{sig.name} ({sig_marker}) "
            f"{'^' if sig.direction == 'up' else 'v' if sig.direction == 'down' else '-'}"
            f"{sig.value:+.2f}"
        )
        if best is None or score > best[0]:
            best = (score, label)
    return best[1] if best else "(no active signal)"


__all__ = [
    "add_scan_subparser",
    "cmd_scan",
]
