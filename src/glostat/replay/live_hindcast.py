from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, Final

import structlog

from glostat.core.errors import ExpertSkipError
from glostat.core.types import ExpertSignal, MarketMeta, Verdict
from glostat.data.data_router import DataRouter
from glostat.data.sec_edgar_client import SecEdgarClient
from glostat.data.sector_stats import SectorStatsBundle, empty_bundle
from glostat.data.snapshot_broker import SnapshotBroker
from glostat.data.universe import Universe
from glostat.data.yfinance_client import (
    YFinanceClient,
    YFinanceDataError,
    YFinanceUnavailableError,
)
from glostat.experts import EFundamentalExpert, EFundFlowExpert, ETimeExpert
from glostat.replay.live_hindcast_network import (
    render_network_summary,
    summarize_network,
    write_network_summary,
)
from glostat.replay.live_sector_stats import make_sector_resolver, resolve_sector_stats
from glostat.verdict_builder import build_verdict

# Sprint 4 PR #2 — live data hindcast. LiveHindcastVerdictBuilder runs the three
# wired Experts via yfinance + SEC EDGAR through DataRouter; LiveActualReturnFetcher
# resolves realised 30-day forward returns with on-disk parquet cache.

log: Final = structlog.get_logger(__name__)

_DEFAULT_CACHE: Final[Path] = Path("cache") / "actual_returns.parquet"
_DEFAULT_SECTOR_STATS_CACHE: Final[Path] = Path("cache") / "sector_stats_live.parquet"
_OHLCV_PADDING_DAYS: Final[int] = 7
_OHLCV_LOOKBACK_DAYS: Final[int] = 1


class SecEdgarUserAgentError(RuntimeError):
    """Raised when SEC EDGAR returns 403 — almost always User-Agent."""


@dataclass(slots=True)
class LiveHindcastVerdictBuilder:
    market_meta: MarketMeta
    horizon_days: int
    yf_client: YFinanceClient
    sec_client: SecEdgarClient
    router: DataRouter
    sector_stats: SectorStatsBundle = field(default_factory=lambda: empty_bundle("live"))
    sector_resolver: Any | None = None
    _locks: dict[str, asyncio.Lock] = field(default_factory=dict)
    _failed_tickers: set[str] = field(default_factory=set)
    _build_count: int = 0
    _failure_count: int = 0
    _skipped_count: int = 0
    _expert_skip_breakdown: dict[str, int] = field(default_factory=dict)

    @property
    def build_count(self) -> int:
        return self._build_count

    @property
    def failure_count(self) -> int:
        return self._failure_count

    @property
    def skipped_count(self) -> int:
        return self._skipped_count

    @property
    def expert_skip_breakdown(self) -> dict[str, int]:
        return dict(self._expert_skip_breakdown)

    @property
    def failed_tickers(self) -> tuple[str, ...]:
        return tuple(sorted(self._failed_tickers))

    def _lock_for(self, ticker: str, day: date) -> asyncio.Lock:
        key = f"{ticker.upper()}|{day.isoformat()}"
        lock = self._locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[key] = lock
        return lock

    async def __call__(self, ticker: str, day: date) -> Verdict | None:
        return await self.build(ticker, day)

    async def build(self, ticker: str, day: date) -> Verdict | None:
        ticker_u = ticker.upper().strip()
        if ticker_u in self._failed_tickers:
            return None
        async with self._lock_for(ticker_u, day):
            try:
                return await self._build_once(ticker_u, day)
            except SecEdgarUserAgentError:
                # WHY: hard-stop signal for the CLI runner — surface so the run aborts.
                raise
            except (YFinanceUnavailableError, YFinanceDataError) as exc:
                log.warning(
                    "live_hindcast.yfinance_skip",
                    ticker=ticker_u, day=day.isoformat(), err=str(exc),
                )
                self._failed_tickers.add(ticker_u)
                self._failure_count += 1
                return None
            except Exception as exc:
                log.warning(
                    "live_hindcast.unexpected",
                    ticker=ticker_u, day=day.isoformat(), err=str(exc),
                )
                self._failure_count += 1
                return None

    async def _build_once(self, ticker: str, day: date) -> Verdict | None:
        ts = datetime(day.year, day.month, day.day, 12, 0, tzinfo=UTC)
        experts = (
            EFundamentalExpert(
                router=self.router,
                sector_stats=self.sector_stats,
                sector_resolver=self.sector_resolver,
            ),
            ETimeExpert(router=self.router),
            EFundFlowExpert(router=self.router),
        )
        signals: list[ExpertSignal] = []
        skipped: list[str] = []
        for expert in experts:
            try:
                sig = await expert.compute(ticker, ts)
            except ExpertSkipError as exc:
                # Sprint 4 PR #3: honest skip — record per-expert and continue.
                # If ALL experts skip the verdict is dropped; if some emit, the
                # verdict is built from survivors (verdict_builder enforces its
                # own min-signal floor).
                skipped.append(expert.name)
                self._expert_skip_breakdown[expert.name] = (
                    self._expert_skip_breakdown.get(expert.name, 0) + 1
                )
                log.info(
                    "live_hindcast.expert_skipped",
                    expert=expert.name, ticker=ticker, day=day.isoformat(),
                    reason=str(exc),
                )
                continue
            except Exception as exc:
                msg = str(exc)
                if "403" in msg or "user-agent" in msg.lower():
                    raise SecEdgarUserAgentError(
                        "SEC EDGAR rejected request (403). "
                        "Check GLOSTAT_SEC_USER_AGENT — must be a real contact."
                    ) from exc
                log.warning(
                    "live_hindcast.expert_failed",
                    expert=expert.name, ticker=ticker, day=day.isoformat(),
                    err=msg,
                )
                continue
            signals.append(sig)
        if not signals:
            # WHY: all experts skipped or failed. Drop the verdict and bump the
            # skipped counter so the harness denominator excludes it.
            self._skipped_count += 1
            log.info(
                "live_hindcast.verdict_skipped",
                ticker=ticker, day=day.isoformat(), skipped_experts=skipped,
            )
            return None
        try:
            verdict = build_verdict(
                ticker=ticker,
                signals=signals,
                market_meta=self.market_meta,
                ts=ts,
                prompt_versions={},
                horizon_days=self.horizon_days,
            )
        except ValueError as exc:
            log.warning(
                "live_hindcast.build_verdict_failed",
                ticker=ticker, day=day.isoformat(), err=str(exc),
            )
            return None
        self._build_count += 1
        return verdict


@dataclass(slots=True)
class LiveActualReturnFetcher:
    yf_client: YFinanceClient
    cache_path: Path = _DEFAULT_CACHE
    _cache: dict[str, float] = field(default_factory=dict)
    _locks: dict[str, asyncio.Lock] = field(default_factory=dict)
    _today: date = field(default_factory=lambda: datetime.now(tz=UTC).date())
    fetch_count: int = 0
    cache_hit_count: int = 0
    dropped_count: int = 0

    def __post_init__(self) -> None:
        self._cache.update(_load_actual_cache(self.cache_path))

    def _lock_for(self, ticker: str, day: date) -> asyncio.Lock:
        key = self._cache_key(ticker, day)
        lock = self._locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[key] = lock
        return lock

    @staticmethod
    def _cache_key(ticker: str, day: date) -> str:
        return f"{ticker.upper()}|{day.isoformat()}"

    async def __call__(self, ticker: str, day: date, horizon_days: int = 30) -> float | None:
        return await self.fetch(ticker, day, horizon_days)

    async def fetch(
        self, ticker: str, day: date, horizon_days: int = 30
    ) -> float | None:
        ticker_u = ticker.upper().strip()
        target_day = day + timedelta(days=horizon_days)
        if target_day > self._today:
            self.dropped_count += 1
            return None
        key = self._cache_key(ticker_u, day)
        if key in self._cache:
            self.cache_hit_count += 1
            return self._cache[key]
        async with self._lock_for(ticker_u, day):
            if key in self._cache:
                self.cache_hit_count += 1
                return self._cache[key]
            try:
                value = await self._fetch_once(ticker_u, day, horizon_days)
            except (YFinanceUnavailableError, YFinanceDataError) as exc:
                log.warning(
                    "live_actual.skip",
                    ticker=ticker_u, day=day.isoformat(), err=str(exc),
                )
                self.dropped_count += 1
                return None
            except Exception as exc:
                log.warning(
                    "live_actual.unexpected",
                    ticker=ticker_u, day=day.isoformat(), err=str(exc),
                )
                self.dropped_count += 1
                return None
            self._cache[key] = value
            self.fetch_count += 1
            return value

    async def _fetch_once(
        self, ticker: str, day: date, horizon_days: int
    ) -> float:
        # WHY: pad the window so weekend/holiday padding yields a real bar at both
        # endpoints. Yahoo's history endpoint is end-exclusive, so add 1 to be safe.
        target_day = day + timedelta(days=horizon_days)
        start = day - timedelta(days=_OHLCV_LOOKBACK_DAYS + _OHLCV_PADDING_DAYS)
        end = target_day + timedelta(days=_OHLCV_PADDING_DAYS + 1)
        series = await self.yf_client.get_ohlcv(ticker, start=start, end=end)
        if not series.bars:
            raise YFinanceDataError(f"no bars for {ticker} {start}..{end}")
        close_at_day = _close_on_or_before(series, day)
        close_at_target = _close_on_or_before(series, target_day)
        if close_at_day is None or close_at_target is None or close_at_day <= 0:
            raise YFinanceDataError(
                f"insufficient bars for {ticker} day={day} target={target_day}"
            )
        return (close_at_target - close_at_day) / close_at_day

    def persist(self) -> Path:
        return _save_actual_cache(self.cache_path, self._cache)


def _close_on_or_before(series: Any, day: date) -> float | None:
    # WHY: pick the latest bar with bar_date ≤ day so weekend/holiday slips to the
    # prior trading day. Returns None when no eligible bar exists.
    best: float | None = None
    best_day: date | None = None
    for bar in series.bars:
        bar_day = bar.ts.date() if hasattr(bar.ts, "date") else bar.ts
        if not isinstance(bar_day, date):
            continue
        if bar_day > day:
            continue
        if best_day is None or bar_day > best_day:
            best_day = bar_day
            best = float(bar.close)
    return best


def _load_actual_cache(path: Path) -> Mapping[str, float]:
    if not path.exists():
        return {}
    try:
        import pyarrow.parquet as pq  # noqa: PLC0415

        table = pq.read_table(path)
        rows = table.to_pylist()
        return {str(r["key"]): float(r["actual_return"]) for r in rows}
    except Exception as exc:
        log.warning("live_actual.cache_load_failed", path=str(path), err=str(exc))
        return {}


def _save_actual_cache(path: Path, cache: Mapping[str, float]) -> Path:
    try:
        import pyarrow as pa  # noqa: PLC0415
        import pyarrow.parquet as pq  # noqa: PLC0415

        path.parent.mkdir(parents=True, exist_ok=True)
        keys = sorted(cache)
        table = pa.Table.from_pylist(
            [{"key": k, "actual_return": float(cache[k])} for k in keys]
        )
        tmp = path.with_suffix(path.suffix + ".tmp")
        pq.write_table(table, tmp, compression="zstd")
        tmp.replace(path)
        log.info("live_actual.cache_saved", path=str(path), rows=len(keys))
    except Exception as exc:
        log.warning("live_actual.cache_save_failed", path=str(path), err=str(exc))
    return path


def build_router(
    *,
    yf_client: YFinanceClient,
    sec_client: SecEdgarClient,
    budget_yaml: Path | None = None,
) -> DataRouter:
    router = DataRouter(budget_yaml=budget_yaml) if budget_yaml else DataRouter()
    router.register_client("yfinance", yf_client)
    router.register_client("sec_edgar", sec_client)
    return router


def make_live_components(
    *,
    market_meta: MarketMeta,
    horizon_days: int,
    snapshot_broker: SnapshotBroker,
    actual_cache_path: Path | None = None,
    sector_stats: SectorStatsBundle | None = None,
    budget_yaml: Path | None = None,
    universe: Universe | None = None,
    sector_stats_cache_path: Path | None = None,
) -> tuple[LiveHindcastVerdictBuilder, LiveActualReturnFetcher, SecEdgarClient]:
    import os  # noqa: PLC0415

    yf_client = YFinanceClient(snapshot_broker=snapshot_broker)
    sec_user_agent = os.environ.get("GLOSTAT_SEC_USER_AGENT")
    sec_client = SecEdgarClient(user_agent=sec_user_agent, snapshot_broker=snapshot_broker)
    router = build_router(yf_client=yf_client, sec_client=sec_client, budget_yaml=budget_yaml)
    # Sprint 5 PR #1 — sector_stats live wiring. If a SectorStatsBundle is not
    # injected explicitly, prefer cache (TTL 7d). Otherwise, when a `universe`
    # is provided, build a fresh bundle from yfinance fundamentals + SEC SIC
    # → GICS resolution; persist to disk so subsequent runs reuse the cost.
    bundle = sector_stats
    if bundle is None:
        bundle = resolve_sector_stats(
            universe=universe,
            yf_client=yf_client,
            sec_client=sec_client,
            cache_path=sector_stats_cache_path or _DEFAULT_SECTOR_STATS_CACHE,
        )
    sector_resolver = make_sector_resolver(sec_client) if universe is not None else None
    builder = LiveHindcastVerdictBuilder(
        market_meta=market_meta,
        horizon_days=horizon_days,
        yf_client=yf_client,
        sec_client=sec_client,
        router=router,
        sector_stats=bundle,
        sector_resolver=sector_resolver,
    )
    fetcher = LiveActualReturnFetcher(
        yf_client=yf_client,
        cache_path=actual_cache_path or _DEFAULT_CACHE,
    )
    return builder, fetcher, sec_client


__all__ = [
    "LiveActualReturnFetcher",
    "LiveHindcastVerdictBuilder",
    "SecEdgarUserAgentError",
    "build_router",
    "make_live_components",
    "render_network_summary",
    "summarize_network",
    "write_network_summary",
]
