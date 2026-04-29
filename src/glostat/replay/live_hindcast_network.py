from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from glostat.replay.live_hindcast import (
        LiveActualReturnFetcher,
        LiveHindcastVerdictBuilder,
    )

# Sprint 4 PR #3 — network summary helpers split out of live_hindcast.py to keep
# both files under the 400-line house rule. The summary now reports retry counts
# and expert skip breakdowns introduced by PR #3.


def _coerce_int(value: Any, default: int = 0) -> int:
    # WHY: tests pass MagicMock-shaped throttles whose attributes are MagicMock,
    # which int() refuses. Surface 0 instead of crashing the summary.
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _retry_stat(client: Any, attr: str) -> int:
    stats = getattr(client, "retry_stats", None)
    if stats is None:
        return 0
    return _coerce_int(getattr(stats, attr, 0))


def summarize_network(
    builder: LiveHindcastVerdictBuilder,
    fetcher: LiveActualReturnFetcher,
) -> dict[str, Any]:
    yf_throttle = builder.yf_client.throttle
    sec_throttle = builder.sec_client.throttle
    return {
        "yfinance_calls": _coerce_int(yf_throttle.acquire_count),
        "yfinance_throttled": _coerce_int(yf_throttle.throttled_count),
        "yfinance_retries": _retry_stat(builder.yf_client, "retry_count"),
        "yfinance_retries_429": _retry_stat(builder.yf_client, "retry_429_count"),
        "yfinance_retries_5xx": _retry_stat(builder.yf_client, "retry_5xx_count"),
        "yfinance_retries_empty": _retry_stat(builder.yf_client, "retry_empty_count"),
        "yfinance_retries_timeout": _retry_stat(builder.yf_client, "retry_timeout_count"),
        "sec_edgar_calls": _coerce_int(sec_throttle.acquire_count),
        "sec_edgar_throttled": _coerce_int(sec_throttle.throttled_count),
        "sec_edgar_retries": _retry_stat(builder.sec_client, "retry_count"),
        "sec_edgar_retries_429": _retry_stat(builder.sec_client, "retry_429_count"),
        "sec_edgar_retries_5xx": _retry_stat(builder.sec_client, "retry_5xx_count"),
        "sec_edgar_retries_timeout": _retry_stat(builder.sec_client, "retry_timeout_count"),
        "actual_return_fetches": _coerce_int(fetcher.fetch_count),
        "actual_return_cache_hits": _coerce_int(fetcher.cache_hit_count),
        "actual_return_dropped": _coerce_int(fetcher.dropped_count),
        "verdicts_built": _coerce_int(builder.build_count),
        "verdicts_skipped": _coerce_int(builder.skipped_count),
        "verdict_failures": _coerce_int(builder.failure_count),
        "expert_skip_breakdown": dict(builder.expert_skip_breakdown),
        "failed_tickers": list(builder.failed_tickers),
    }


def render_network_summary(summary: Mapping[str, Any]) -> str:
    lines: list[str] = ["=== Network call summary ==="]
    for key in (
        "yfinance_calls",
        "yfinance_throttled",
        "yfinance_retries",
        "sec_edgar_calls",
        "sec_edgar_throttled",
        "sec_edgar_retries",
        "actual_return_fetches",
        "actual_return_cache_hits",
        "actual_return_dropped",
        "verdicts_built",
        "verdicts_skipped",
        "verdict_failures",
    ):
        lines.append(f"  {key:<26} {summary.get(key, 0)}")
    breakdown = summary.get("expert_skip_breakdown") or {}
    if breakdown:
        ordered = ", ".join(f"{k}={v}" for k, v in sorted(breakdown.items()))
        lines.append(f"  expert_skip_breakdown      {ordered}")
    failed = summary.get("failed_tickers") or []
    if failed:
        lines.append(f"  failed_tickers             {','.join(str(t) for t in failed)}")
    return "\n".join(lines)


def write_network_summary(path: Path, summary: Mapping[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(dict(summary), sort_keys=True, indent=2), encoding="utf-8")
    return path


__all__ = [
    "render_network_summary",
    "summarize_network",
    "write_network_summary",
]
