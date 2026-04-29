from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections.abc import Sequence
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, Final

import structlog

from glostat.cli_hindcast_live import run_live_hindcast
from glostat.cli_hindcast_report import (
    json_payload,
    persist_reports,
    render_kill_decision,
    render_metrics_table,
)
from glostat.cli_mock_universe import synthetic_actual_30d_return, synthetic_signal_seed
from glostat.core.types import ExpertSignal, MarketMeta, Verdict
from glostat.data.universe import load_universe
from glostat.replay.kill_criteria import (
    HindcastMetricsView,
    KillCriteriaMonitor,
    KillDecision,
)
from glostat.replay.live_hindcast import (
    render_network_summary,
    write_network_summary,
)
from glostat.replay.sprint4_gate import (
    evaluate_sprint4_gate,
    render_gate_table,
)
from glostat.replay.validation_harness import Hindcast
from glostat.risk.compliance_gate import disclaimer_for
from glostat.verdict_builder import build_verdict

# Sprint 4 PR #1 + PR #2 — `glostat hindcast` + `glostat gate-status` subcommands.
# Mock-mode: deterministic synthetic ExpertSignals + actual returns.
# Live-mode (PR #2): real yfinance + SEC EDGAR through DataRouter; snapshots
# persist via SnapshotBroker; actual returns from yfinance OHLCV with cache.

log: Final = structlog.get_logger(__name__)

_DEFAULT_HINDCAST_CACHE: Final[Path] = Path("cache") / "hindcast"
_DEFAULT_SNAPSHOT_ROOT: Final[Path] = Path("cache") / "snapshots"
_DEFAULT_ACTUAL_CACHE: Final[Path] = Path("cache") / "actual_returns.parquet"
_DEFAULT_UNIVERSE: Final[str] = "US_LARGE_SAMPLE"
_DEFAULT_HORIZON_DAYS: Final[int] = 30
_DEFAULT_MAX_CONCURRENT: Final[int] = 5


def add_hindcast_subparser(sub: Any) -> None:
    p = sub.add_parser(
        "hindcast",
        help="Run Sprint 4 hindcast gate over a date range.",
    )
    p.add_argument("--start", required=True, help="ISO date YYYY-MM-DD (inclusive).")
    p.add_argument("--end",   required=True, help="ISO date YYYY-MM-DD (inclusive).")
    p.add_argument("--universe", default=_DEFAULT_UNIVERSE,
                   help=f"Universe name (default {_DEFAULT_UNIVERSE}).")
    p.add_argument("--mock", action="store_true",
                   help="Use synthetic deterministic signals + actuals.")
    p.add_argument("--tickers", default=None,
                   help="Comma-separated subset (overrides --universe ticker list).")
    p.add_argument("--max-concurrent", type=int, default=_DEFAULT_MAX_CONCURRENT,
                   help=f"Parallel ticker semaphore. Default {_DEFAULT_MAX_CONCURRENT}.")
    p.add_argument("--split", type=float, default=0.7,
                   help="IS/OOS split ratio in [0.5, 0.9]. Default 0.7.")
    p.add_argument("--profile", default="cautious",
                   choices=["cautious", "balanced", "aggressive"],
                   help="Kill criteria profile. Default cautious.")
    p.add_argument("--horizon", type=int, default=_DEFAULT_HORIZON_DAYS,
                   help="Forward return horizon in days. Default 30.")
    p.add_argument("--jurisdiction", default="US",
                   choices=["KR", "US", "EU", "JP", "TW", "HK", "DEFAULT"])
    p.add_argument("--defer-shutdown-7d", action="store_true",
                   help="INV-GS-033 explicit deferral flag (cannot be silent).")
    p.add_argument("--report-dir", default=None,
                   help="Override report output dir (default cache/hindcast).")
    p.add_argument("--snapshot-root", default=None,
                   help="Override snapshot broker root (default cache/snapshots).")
    p.add_argument("--actual-cache", default=None,
                   help="Override actual-return parquet cache path.")
    p.add_argument("--json", action="store_true",
                   help="Emit machine-readable JSON to stdout.")


def add_gate_status_subparser(sub: Any) -> None:
    p = sub.add_parser(
        "gate-status",
        help="Print last cached Sprint 4 gate decision.",
    )
    p.add_argument("--report-dir", default=None,
                   help="Override report dir (default cache/hindcast).")


def cmd_hindcast(args: argparse.Namespace) -> int:
    start = _parse_date(args.start)
    end = _parse_date(args.end)
    if end <= start:
        print("--end must be after --start", file=sys.stderr)
        return 2

    universe = load_universe(args.universe)
    tickers = _resolve_tickers(universe.tickers, args.tickers)
    if not tickers:
        print("no tickers selected (check --tickers and --universe).", file=sys.stderr)
        return 2

    market_meta = _load_market_meta_xnas()
    network_summary: dict[str, Any] = {}
    aborted_reason: str | None = None

    if args.mock:
        builder = _MockHindcastVerdictBuilder(
            market_meta=market_meta, horizon_days=args.horizon,
        )
        report = _run_mock_hindcast(builder, tickers, args, start, end)
    else:
        live_result = run_live_hindcast(
            market_meta=market_meta,
            horizon_days=args.horizon,
            tickers=tickers,
            start_date=start,
            end_date=end,
            split=args.split,
            parallel_tickers=args.max_concurrent,
            snapshot_root=Path(args.snapshot_root or _DEFAULT_SNAPSHOT_ROOT),
            actual_cache=Path(args.actual_cache or _DEFAULT_ACTUAL_CACHE),
            universe=universe,
        )
        report = live_result["report"]
        network_summary = live_result["summary"]
        aborted_reason = live_result["aborted_reason"]

    if aborted_reason is not None or report is None:
        print(f"[glostat] hindcast ABORTED: {aborted_reason}", file=sys.stderr)
        if network_summary:
            print(render_network_summary(network_summary), file=sys.stderr)
        return 1

    return _render_and_persist(args, report, network_summary)


def cmd_gate_status(args: argparse.Namespace) -> int:
    report_dir = Path(args.report_dir or _DEFAULT_HINDCAST_CACHE)
    files = sorted(report_dir.glob("sprint4_*_report.json"))
    if not files:
        print(f"no cached gate reports under {report_dir}/", file=sys.stderr)
        return 1
    latest = files[-1]
    payload = json.loads(latest.read_text("utf-8"))
    print(f"=== Sprint 4 gate-status ({latest.name}) ===")
    print(f"  pass_status        : {payload['gate']['pass_status']}")
    print(f"  profile            : {payload['gate']['profile']}")
    print(f"  v031_pivot_eligible: {payload['gate']['v031_pivot_eligible']}")
    print(f"  reasoning          : {payload['gate']['reasoning']}")
    print(f"  decision           : {payload['kill']['decision']}")
    print("  recommendations    :")
    for r in payload["kill"]["recommendations"]:
        print(f"    - {r}")
    return 0


# ── helpers ────────────────────────────────────────────────────────────────


def _render_and_persist(
    args: argparse.Namespace, report: Any, network_summary: dict[str, Any]
) -> int:
    gate = evaluate_sprint4_gate(
        sharpe=report.overall_sharpe,
        oos_degradation=report.degradation(),
        auc=report.overall_auc,
        cost_passed_pct=report.cost_passed_pct,
        maxdd=report.overall_maxdd,
        reproducibility=report.reproducibility,
        n_verdicts=report.n_verdicts,
        profile=args.profile,
    )
    monitor = KillCriteriaMonitor(profile=args.profile)
    metrics_view = HindcastMetricsView(
        sharpe=report.overall_sharpe,
        oos_degradation=report.degradation(),
        auc=report.overall_auc,
        cost_passed_pct=report.cost_passed_pct,
        maxdd=report.overall_maxdd,
        consecutive_violation_days=monitor.thresholds.grace_period_consecutive_days,
        consecutive_oos_cycles_failed=2,
    )
    kill = monitor.evaluate(metrics_view, defer_shutdown=args.defer_shutdown_7d)

    report_dir = Path(args.report_dir or _DEFAULT_HINDCAST_CACHE)
    paths = persist_reports(report, gate, kill, report_dir, profile=args.profile)
    log.info("hindcast.report_saved", json=str(paths["json"]), md=str(paths["md"]))

    if network_summary:
        net_path = report_dir / paths["json"].name.replace("_report.json", "_network.json")
        write_network_summary(net_path, network_summary)
        paths["network"] = net_path

    if args.json:
        print(json_payload(report, gate, kill, args.profile, compact=True))
    else:
        print(render_metrics_table(report))
        print()
        print(render_gate_table(gate))
        print()
        print(render_kill_decision(kill))
        print()
        if network_summary:
            print(render_network_summary(network_summary))
            print()
        print(f"Report saved: {paths['json']}")
        print(f"Report saved: {paths['md']}")
        if "network" in paths:
            print(f"Report saved: {paths['network']}")
        print()
        print(disclaimer_for(args.jurisdiction).render(
            ticker="*", action="*", issued_at=report.split.in_sample_start.isoformat(),
        ))

    if kill.decision is KillDecision.SHUTDOWN and not args.defer_shutdown_7d:
        return 1
    return 0


def _resolve_tickers(
    universe_tickers: Sequence[str], tickers_arg: str | None
) -> tuple[str, ...]:
    if tickers_arg:
        wanted = tuple(t.strip().upper() for t in tickers_arg.split(",") if t.strip())
        return tuple(t for t in wanted if t)
    return tuple(universe_tickers)


def _run_mock_hindcast(
    builder: _MockHindcastVerdictBuilder,
    tickers: Sequence[str],
    args: argparse.Namespace,
    start: date,
    end: date,
) -> Any:
    async def verdict_for_day(ticker: str, day: date) -> Verdict | None:
        return builder.build(ticker, day)

    async def actual_for(ticker: str, day: date, h: int) -> float:
        return synthetic_actual_30d_return(ticker, day, horizon_days=h)

    hc = Hindcast(
        pipeline=None,
        universe=tickers,
        verdict_for_day=verdict_for_day,
        actual_return_for=actual_for,
        horizon_days=args.horizon,
        parallel_tickers=max(1, int(args.max_concurrent)),
    )
    return hc.run(start_date=start, end_date=end, split=args.split)


# ── mock verdict builder (Sprint 4 PR #1) ──────────────────────────────────


class _MockHindcastVerdictBuilder:
    def __init__(self, *, market_meta: MarketMeta, horizon_days: int) -> None:
        self._market_meta = market_meta
        self._horizon_days = horizon_days

    def build(self, ticker: str, day: date) -> Verdict | None:
        h = hashlib.sha256(synthetic_signal_seed(ticker, day).encode()).digest()
        score_byte = h[0]
        if score_byte < 51:
            net = 1.10 + (h[1] / 255.0) * 0.30
            target_dir = "LONG"
        elif score_byte < 76:
            net = -(1.10 + (h[1] / 255.0) * 0.30)
            target_dir = "SHORT"
        else:
            net = ((h[1] / 255.0) - 0.5) * 0.026
            target_dir = "NEUTRAL"
        confidence = min(1.0, abs(net) / 2.0 + 0.20)
        archetype = "impulse" if net >= 0 else "contrarian"
        ts = datetime(day.year, day.month, day.day, 12, 0, tzinfo=UTC)
        signals: list[ExpertSignal] = []
        for i, name in enumerate(("E_FUNDAMENTAL", "E_TIME", "E_FUND_FLOW")):
            jitter_amp = 0.05 if target_dir != "NEUTRAL" else 0.15
            jitter = ((h[2 + i] / 255.0) - 0.5) * jitter_amp
            sig_net = max(-3.0, min(3.0, net + jitter))
            sig_dir = "LONG" if sig_net > 1.0 else "SHORT" if sig_net < -1.0 else "NEUTRAL"
            signals.append(
                ExpertSignal(
                    expert_name=name,  # type: ignore[arg-type]
                    ticker=ticker,
                    direction=sig_dir,  # type: ignore[arg-type]
                    net_score=sig_net,
                    confidence=confidence,
                    archetype=archetype,  # type: ignore[arg-type]
                    basis=f"hindcast.mock {name} day={day.isoformat()} score={sig_net:.2f}",
                    sources=(_synthetic_snapshot_id(ticker, day, name),),
                    expires_at=ts,
                    metadata=(("hindcast_mock", "true"),),
                )
            )
        try:
            return build_verdict(
                ticker=ticker,
                signals=signals,
                market_meta=self._market_meta,
                ts=ts,
                prompt_versions={},
                horizon_days=self._horizon_days,
            )
        except ValueError as exc:
            log.warning(
                "hindcast.build_verdict_failed",
                ticker=ticker, day=day.isoformat(), err=str(exc),
            )
            return None


def _synthetic_snapshot_id(ticker: str, day: date, expert: str) -> str:
    base = f"GLOSTAT/hindcast/snap/{expert}/{ticker.upper()}|{day.isoformat()}"
    return hashlib.sha256(base.encode()).hexdigest()


def _load_market_meta_xnas() -> MarketMeta:
    from glostat.cli import _load_market_meta  # noqa: PLC0415 — local import to avoid cycle
    return _load_market_meta("XNAS")


def _parse_date(s: str) -> date:
    try:
        return date.fromisoformat(s)
    except ValueError as exc:
        raise SystemExit(f"invalid date {s!r}: {exc}") from exc


__all__ = [
    "add_gate_status_subparser",
    "add_hindcast_subparser",
    "cmd_gate_status",
    "cmd_hindcast",
]
