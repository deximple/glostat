"""v1.10.15 — E_ANALYST_REVISION hindcast (US large-cap basket).

E_ANALYST_REVISION expert measures sell-side analyst rating revision drift
via yfinance Ticker.upgrades_downgrades. The expert is point-in-time-aware
(events filtered by `today` parameter) so a hindcast iterating sample days
is straightforward.

This script provides the missing harness for v1.10.15 — without it,
E_ANALYST_REVISION sat at bootstrap (n=0, weight=0) since v1.8.0.

Universe: SP500 top50 (matches phase1b PEAD/sector_rotation).
Window:   2024-01-02..2026-03-29 default.
Stride:   7 days (paper-style weekly sample).
Horizon:  30 days (matches expert default _HORIZON_DAYS).

Output: cache/hindcast/phase_us_analyst_revision/e_analyst_revision_report.json
        — calibration loader picks up via _PHASE_SOURCES (added in this
        commit).

Usage:
  uv run python scripts/run_analyst_revision_hindcast.py
"""
from __future__ import annotations

import asyncio
import json
import sys
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import structlog

from glostat.data.yfinance_client import YFinanceClient
from glostat.experts.e_analyst_revision import score_revisions
from glostat.phase1b.cli import load_tickers
from glostat.replay.metrics import annualized_sharpe, auc_roc

log = structlog.get_logger(__name__)

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SP500_PATH = _REPO_ROOT / "configs" / "universes" / "sp500_top50.txt"
_OUT_DIR = _REPO_ROOT / "cache" / "hindcast" / "phase_us_analyst_revision"
_OHLCV_PADDING = 14
_HORIZON_DAYS = 30
_DEFAULT_STRIDE = 7
_DEFAULT_SPLIT = 0.7
_DIRECTION_THRESHOLD = 0.6   # mirrors expert _DIRECTION_THRESHOLD


def _sample_days(*, start: date, end: date, stride: int) -> list[date]:
    out: list[date] = []
    cur = start
    while cur <= end:
        if cur.weekday() < 5:
            out.append(cur)
        cur += timedelta(days=stride)
    return out


def _close_on_or_before(bars, day: date) -> float | None:
    best, best_day = None, None
    for b in bars:
        bd = b.ts.date() if hasattr(b.ts, "date") else b.ts
        if not isinstance(bd, date):
            continue
        if bd > day:
            continue
        if best_day is None or bd > best_day:
            best, best_day = float(b.close or 0.0), bd
    return best if best and best > 0 else None


async def main(start: date, end: date, stride: int) -> int:  # noqa: PLR0915
    yf = YFinanceClient()
    tickers = load_tickers(_SP500_PATH)
    log.info("analyst_rev.tickers", n=len(tickers))

    # 1) Fetch full recommendation history per ticker (point-in-time
    #    filtered by event timestamp at scoring time).
    rec_history: dict[str, tuple] = {}
    for t in tickers:
        try:
            hist = await yf.get_recommendations(t)
            rec_history[t] = tuple(hist.events)
        except Exception as exc:
            log.info("analyst_rev.rec_failed", ticker=t, err=str(exc))
            rec_history[t] = ()
    log.info(
        "analyst_rev.rec_loaded",
        with_events=sum(1 for evs in rec_history.values() if evs),
    )

    # 2) Iterate sample days × tickers; score using point-in-time events.
    sample_days = _sample_days(start=start, end=end, stride=stride)
    today = datetime.now(tz=UTC).date()
    trades: list[tuple[date, str, float, str, float]] = []
    n_evaluated = 0
    n_skipped = 0
    skip_breakdown: dict[str, int] = {}

    def record_skip(reason: str) -> None:
        nonlocal n_skipped
        n_skipped += 1
        skip_breakdown[reason] = skip_breakdown.get(reason, 0) + 1

    # Pre-fetch OHLCV per ticker (for forward returns).
    ohlcv: dict[str, tuple] = {}
    for t in tickers:
        try:
            series = await yf.get_ohlcv(
                t, start=start - timedelta(days=_OHLCV_PADDING),
                end=end + timedelta(days=_OHLCV_PADDING + 1),
            )
            ohlcv[t] = tuple(series.bars)
        except Exception as exc:
            log.info("analyst_rev.ohlcv_failed", ticker=t, err=str(exc))
            ohlcv[t] = ()

    for day in sample_days:
        if day + timedelta(days=_HORIZON_DAYS) > today:
            continue
        ts = datetime(day.year, day.month, day.day, 12, 0, tzinfo=UTC)
        for ticker in tickers:
            n_evaluated += 1
            events = rec_history.get(ticker, ())
            if not events:
                record_skip("no_recommendations")
                continue
            # Filter events to ≤ day (point-in-time).
            pit_events = tuple(e for e in events if e.ts.date() <= day)
            if not pit_events:
                record_skip("no_pit_events")
                continue
            score = score_revisions(pit_events, today=ts)
            if score.net_score == 0 or abs(score.net_score) < _DIRECTION_THRESHOLD:
                record_skip("below_direction_threshold")
                continue
            direction = "LONG" if score.net_score > 0 else "SHORT"
            # Forward 30d return.
            bars = ohlcv.get(ticker, ())
            p0 = _close_on_or_before(bars, day)
            p1 = _close_on_or_before(bars, day + timedelta(days=_HORIZON_DAYS))
            if p0 is None or p1 is None:
                record_skip("no_forward_return")
                continue
            fwd = (p1 - p0) / p0
            trades.append((day, ticker, score.raw_score, direction, fwd))

    # 3) IS/OOS split + aggregate metrics.
    trades.sort(key=lambda t: t[0])
    n = len(trades)
    split_idx = int(n * _DEFAULT_SPLIT)
    is_t, oos_t = trades[:split_idx], trades[split_idx:]

    def _auc(t_list) -> float:
        if len(t_list) < 5:
            return 0.5
        scores = [(1.0 if d == "LONG" else -1.0) * s for _, _, s, d, _ in t_list]
        labels = [1 if f > 0 else 0 for _, _, _, _, f in t_list]
        return auc_roc(scores, labels)

    def _sharpe(t_list) -> float:
        if len(t_list) < 2:
            return 0.0
        rets = [(f if d == "LONG" else -f) for _, _, _, d, f in t_list]
        periods_per_year = max(1, int(252 / _HORIZON_DAYS))
        return annualized_sharpe(rets, periods_per_year=periods_per_year)

    is_auc, oos_auc, overall_auc = _auc(is_t), _auc(oos_t), _auc(trades)
    is_sharpe, oos_sharpe, overall_sharpe = _sharpe(is_t), _sharpe(oos_t), _sharpe(trades)
    oos_deg = 1.0 if is_sharpe <= 0 else max(0.0, 1.0 - oos_sharpe / is_sharpe)

    payload = {
        "report": {
            "expert": "E_ANALYST_REVISION",
            "universe": tickers,
            "period_start": start.isoformat(),
            "period_end": end.isoformat(),
            "horizon_days": _HORIZON_DAYS,
            "n_universe": len(tickers),
            "n_evaluated": n_evaluated,
            "n_signals": len(trades),
            "n_trades": len(trades),
            "n_skipped": n_skipped,
            "is_auc": is_auc,
            "oos_auc": oos_auc,
            "overall_auc": overall_auc,
            "is_sharpe": is_sharpe,
            "oos_sharpe": oos_sharpe,
            "overall_sharpe": overall_sharpe,
            "skip_breakdown": skip_breakdown,
            "notes": [
                f"split_ratio={_DEFAULT_SPLIT:.2f} is={len(is_t)} oos={len(oos_t)}",
                f"skip_rate={n_skipped / max(1, n_evaluated):.2%}",
                "trigger=|net_score|>=_DIRECTION_THRESHOLD (≈3 net revisions)",
                "stride=7 horizon=30d basket=per-trade (not basket aggregated)",
            ],
        }
    }
    _OUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = _OUT_DIR / "e_analyst_revision_report.json"
    out_path.write_text(json.dumps(payload, indent=2, default=str))

    print("=" * 70)
    print("E_ANALYST_REVISION hindcast complete (universe=SP500 top50)")
    print("=" * 70)
    print(f"  n_evaluated   : {n_evaluated}")
    print(f"  n_skipped     : {n_skipped}")
    print(f"  n_trades      : {len(trades)}")
    print(f"  AUC overall   : {overall_auc:.4f}")
    print(f"  AUC IS / OOS  : {is_auc:.4f} / {oos_auc:.4f}")
    print(f"  Sharpe        : {overall_sharpe:+.4f}")
    print(f"  Sharpe IS/OOS : {is_sharpe:+.4f} / {oos_sharpe:+.4f}")
    print(f"  OOS_deg       : {oos_deg:.4f}")
    if skip_breakdown:
        print(f"  skip top: {sorted(skip_breakdown.items(), key=lambda kv: -kv[1])[:3]}")
    print(f"  → {out_path}")
    return 0


if __name__ == "__main__":
    s = date.fromisoformat(sys.argv[1]) if len(sys.argv) > 1 else date(2024, 1, 2)
    e = date.fromisoformat(sys.argv[2]) if len(sys.argv) > 2 else date(2026, 3, 29)
    st = int(sys.argv[3]) if len(sys.argv) > 3 else _DEFAULT_STRIDE
    raise SystemExit(asyncio.run(main(s, e, st)))
