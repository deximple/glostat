from __future__ import annotations

import asyncio
import json
import time
from datetime import UTC, date, datetime
from typing import Any, Final

import structlog

from glostat.data.retry import RetryStats, with_retry
from glostat.data.snapshot_broker import SnapshotBroker, SnapshotKey
from glostat.data.yfinance_parsers import (
    dividends_to_payload,
    earnings_to_payload,
    fundamentals_to_payload,
    holders_to_payload,
    ohlcv_to_payload,
    parse_dividends,
    parse_earnings_calendar,
    parse_fundamentals,
    parse_holders,
    parse_ohlcv,
)
from glostat.data.yfinance_types import (
    DividendEvent,
    DividendHistory,
    EarningsCalendar,
    EarningsEvent,
    Fundamentals,
    HoldersKind,
    HoldersSnapshot,
    OhlcvBar,
    OhlcvSeries,
)

# Free-stack OHLCV / fundamentals / dividends / earnings client backed by yfinance.
# v0.6 §1.3 + Sprint 4 PR #3: 8 req/sec self-throttle (INV-GS-037, Yahoo unofficial
# endpoint protection — relaxed from 5 to 8 after PR #2 hit 93% throttle ratio;
# Yahoo's unofficial cap research suggests 10-20/s is fine for read-only IPs).
# Retry: exponential backoff on empty bodies + 429/5xx/timeouts (with_retry helper).
# Sprint 1 PR #1: ohlcv + fundamentals.  Sprint 1 PR #2: dividends + earnings_calendar.

log: Final = structlog.get_logger(__name__)

_RATE_LIMIT_PER_SEC: Final[int] = 8
_MIN_INTERVAL_S: Final[float] = 1.0 / _RATE_LIMIT_PER_SEC


def _yf_ticker(ticker: str) -> str:
    # v1.1 K1 — KR 6-digit codes need .KS suffix for yfinance (INV-GS-106).
    # Bare US tickers and pre-suffixed KR/EU/JP names pass through unchanged.
    t = (ticker or "").strip().upper()
    if not t:
        return t
    if t.endswith(".KS") or t.endswith(".KQ"):
        return t
    if len(t) == 6 and t.isdigit():
        return t + ".KS"
    return t


def _uaid_for(yf_ticker: str) -> str:
    # WHY: snapshot UAID encodes the originating market so dedup keys stay
    # collision-free between (XKRX, 005930) and a hypothetical US ticker. KR
    # uses bare 6-digit code (no suffix) inside UAID for canonical form.
    if yf_ticker.endswith(".KS"):
        return f"XKRX.{yf_ticker[:-3]}"
    if yf_ticker.endswith(".KQ"):
        return f"XKOS.{yf_ticker[:-3]}"
    return f"XNAS.{yf_ticker}"


class YFinanceUnavailableError(NotImplementedError):
    """Raised when yfinance package isn't installed or refuses to load."""


class YFinanceDataError(RuntimeError):
    """Raised when yfinance returns an empty or malformed response."""


class _Throttle:
    # WHY: simple semaphore + monotonic clock — fairer than per-call sleep under contention.
    def __init__(self, *, rate_per_sec: int = _RATE_LIMIT_PER_SEC) -> None:
        self._sem = asyncio.Semaphore(rate_per_sec)
        self._lock = asyncio.Lock()
        self._next_slot: float = 0.0
        self.acquire_count: int = 0
        self.throttled_count: int = 0

    async def acquire(self) -> None:
        await self._sem.acquire()
        async with self._lock:
            now = time.monotonic()
            wait = max(0.0, self._next_slot - now)
            if wait > 0:
                self.throttled_count += 1
            self._next_slot = max(now, self._next_slot) + _MIN_INTERVAL_S
            self.acquire_count += 1
        if wait > 0:
            await asyncio.sleep(wait)

    def release(self) -> None:
        self._sem.release()


def _import_yfinance() -> Any:
    # WHY: defer import — keeps module importable in MVP environments where yfinance
    # may be absent. Surfaces actionable error message at first use site.
    try:
        import yfinance as yf  # noqa: PLC0415 — deferred for graceful degradation
    except ImportError as exc:
        raise YFinanceUnavailableError(
            "yfinance not installed. Install with: pip install yfinance>=0.2.40"
        ) from exc
    return yf


class YFinanceClient:
    def __init__(self, *, snapshot_broker: SnapshotBroker | None = None) -> None:
        self._broker = snapshot_broker
        self._throttle = _Throttle()
        self._last_snapshot_id: str | None = None
        self._retry_stats = RetryStats()

    @property
    def throttle(self) -> _Throttle:
        return self._throttle

    @property
    def retry_stats(self) -> RetryStats:
        return self._retry_stats

    @property
    def last_snapshot_id(self) -> str | None:
        # WHY: experts read this immediately after fetch to record sources.
        return self._last_snapshot_id

    def attach_snapshot_broker(self, broker: SnapshotBroker) -> None:
        self._broker = broker

    # ── public surface ─────────────────────────────────────────────────────

    async def get_ohlcv(
        self,
        ticker: str,
        *,
        start: date,
        end: date,
        interval: str = "1d",
    ) -> OhlcvSeries:
        yf_ticker = _yf_ticker(ticker)

        async def _fetch() -> list[OhlcvBar]:
            await self._throttle.acquire()
            try:
                yf = _import_yfinance()
                return await asyncio.to_thread(parse_ohlcv, yf, yf_ticker, start, end, interval)
            finally:
                self._throttle.release()

        bars = await with_retry(
            _fetch,
            stats=self._retry_stats,
            is_empty=lambda b: not b,
            operation=f"yfinance.history:{yf_ticker}",
        )
        if not bars:
            raise YFinanceDataError(
                f"yfinance returned empty OHLCV for {yf_ticker} {start}..{end}"
            )
        series = OhlcvSeries(ticker=yf_ticker, bars=tuple(bars), interval=interval)
        self._record_snapshot(
            tool="yfinance.history",
            uaid=_uaid_for(yf_ticker),
            edge_type="ohlcv",
            ts=bars[-1].ts if bars[-1].ts.tzinfo else bars[-1].ts.replace(tzinfo=UTC),
            params={
                "ticker": yf_ticker,
                "start": start.isoformat(),
                "end": end.isoformat(),
                "interval": interval,
            },
            payload=ohlcv_to_payload(series),
        )
        return series

    async def get_fundamentals(self, ticker: str) -> Fundamentals:
        yf_ticker = _yf_ticker(ticker)

        async def _fetch() -> Fundamentals:
            await self._throttle.acquire()
            try:
                yf = _import_yfinance()
                return await asyncio.to_thread(parse_fundamentals, yf, yf_ticker)
            finally:
                self._throttle.release()

        f = await with_retry(
            _fetch,
            stats=self._retry_stats,
            operation=f"yfinance.info:{yf_ticker}",
        )
        self._record_snapshot(
            tool="yfinance.info",
            uaid=_uaid_for(yf_ticker),
            edge_type="fundamentals",
            ts=datetime.now(tz=UTC),
            params={"ticker": yf_ticker},
            payload=fundamentals_to_payload(f),
        )
        return f

    async def get_dividends(self, ticker: str) -> DividendHistory:
        yf_ticker = _yf_ticker(ticker)

        async def _fetch() -> list[DividendEvent]:
            await self._throttle.acquire()
            try:
                yf = _import_yfinance()
                return await asyncio.to_thread(parse_dividends, yf, yf_ticker)
            finally:
                self._throttle.release()

        events = await with_retry(
            _fetch,
            stats=self._retry_stats,
            operation=f"yfinance.dividends:{yf_ticker}",
        )
        history = DividendHistory(ticker=yf_ticker, events=tuple(events))
        self._record_snapshot(
            tool="yfinance.dividends",
            uaid=_uaid_for(yf_ticker),
            edge_type="dividends",
            ts=datetime.now(tz=UTC),
            params={"ticker": yf_ticker},
            payload=dividends_to_payload(history),
        )
        return history

    async def get_recommendations(self, ticker: str) -> Any:
        # v1.8.0 — sell-side analyst recommendation history. Imports
        # AnalystRecommendationHistory locally to avoid widening the
        # module-level import set.
        from glostat.data.yfinance_parsers import (  # noqa: PLC0415
            parse_recommendations,
            recommendations_to_payload,
        )
        from glostat.data.yfinance_types import (  # noqa: PLC0415
            AnalystRecommendationHistory,
        )
        yf_ticker = _yf_ticker(ticker)

        async def _fetch() -> list[Any]:
            await self._throttle.acquire()
            try:
                yf = _import_yfinance()
                return await asyncio.to_thread(
                    parse_recommendations, yf, yf_ticker,
                )
            finally:
                self._throttle.release()

        events = await with_retry(
            _fetch,
            stats=self._retry_stats,
            operation=f"yfinance.recommendations:{yf_ticker}",
        )
        history = AnalystRecommendationHistory(
            ticker=yf_ticker, events=tuple(events),
        )
        self._record_snapshot(
            tool="yfinance.recommendations",
            uaid=_uaid_for(yf_ticker),
            edge_type="analyst_recommendations",
            ts=datetime.now(tz=UTC),
            params={"ticker": yf_ticker},
            payload=recommendations_to_payload(history),
        )
        return history

    async def get_earnings_calendar(self, ticker: str) -> EarningsCalendar:
        yf_ticker = _yf_ticker(ticker)

        async def _fetch() -> list[EarningsEvent]:
            await self._throttle.acquire()
            try:
                yf = _import_yfinance()
                return await asyncio.to_thread(parse_earnings_calendar, yf, yf_ticker)
            finally:
                self._throttle.release()

        events = await with_retry(
            _fetch,
            stats=self._retry_stats,
            operation=f"yfinance.calendar:{yf_ticker}",
        )
        calendar = EarningsCalendar(ticker=yf_ticker, upcoming=tuple(events))
        self._record_snapshot(
            tool="yfinance.calendar",
            uaid=_uaid_for(yf_ticker),
            edge_type="earnings_calendar",
            ts=datetime.now(tz=UTC),
            params={"ticker": yf_ticker},
            payload=earnings_to_payload(calendar),
        )
        return calendar

    async def get_holders(
        self, ticker: str, *, kind: HoldersKind = "institutional"
    ) -> HoldersSnapshot:
        async def _fetch() -> list[tuple[str, float, int, str]]:
            await self._throttle.acquire()
            try:
                yf = _import_yfinance()
                return await asyncio.to_thread(parse_holders, yf, ticker, kind)
            finally:
                self._throttle.release()

        rows = await with_retry(
            _fetch,
            stats=self._retry_stats,
            operation=f"yfinance.holders:{ticker.upper()}",
        )
        now = datetime.now(tz=UTC)
        holders = tuple((name, pct) for (name, pct, _shares, _ts) in rows)
        snap = HoldersSnapshot(
            ticker=ticker.upper(), kind=kind, holders=holders, fetched_at=now,
            rows=tuple(rows),
        )
        self._record_snapshot(
            tool="yfinance.holders",
            uaid=f"XNAS.{ticker.upper()}",
            edge_type=f"holders.{kind}",
            ts=now,
            params={"ticker": ticker.upper(), "kind": kind},
            payload=holders_to_payload(snap),
        )
        return snap

    # ── snapshot integration ───────────────────────────────────────────────

    def _record_snapshot(
        self,
        *,
        tool: str,
        uaid: str,
        edge_type: str,
        ts: datetime,
        params: dict[str, Any],
        payload: dict[str, Any],
    ) -> None:
        if self._broker is None:
            self._last_snapshot_id = None
            return
        params_canon = json.dumps(params, sort_keys=True, separators=(",", ":"))
        key = SnapshotKey(
            uaid=uaid,
            edge_type=edge_type,
            ts_utc=ts,
            tool=tool,
            params_canon=params_canon,
        )
        rec = self._broker.save_snapshot(key, payload)
        self._last_snapshot_id = rec.leaf.leaf_hash


__all__ = [
    "_MIN_INTERVAL_S",
    "_RATE_LIMIT_PER_SEC",
    "DividendEvent",
    "DividendHistory",
    "EarningsCalendar",
    "EarningsEvent",
    "Fundamentals",
    "HoldersKind",
    "HoldersSnapshot",
    "OhlcvBar",
    "OhlcvSeries",
    "YFinanceClient",
    "YFinanceDataError",
    "YFinanceUnavailableError",
]
