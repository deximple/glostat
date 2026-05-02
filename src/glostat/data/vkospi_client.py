from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Final

import structlog

from glostat.core.errors import GlostatError
from glostat.data.snapshot_broker import SnapshotBroker, SnapshotKey

# v1.10.6 — VKOSPI (KOSPI 200 implied volatility index) client.
#
# WHY: Lee, Son, Lee (2024) "VKOSPI 지수를 이용한 단기주가수익률 예측에 관한
# 연구" (금융정보연구 2024.02) documents asymmetric KR market behaviour around
# large price shocks — VKOSPI↑ + price↓ produces reversal, VKOSPI↓ + price↑
# produces drift continuation, both with p<0.001 over 18 years (KOSPI 200,
# 4,976 raw / 3,010 abnormal-return events). E_VKOSPI_MOOD_KR is the GLOSTAT
# thesis encoding of that finding; this client is the data-plumbing layer.
#
# Data source landscape (free, no API key, KR-public):
#   - KRX Information Data System
#       https://data.krx.co.kr/
#       Endpoint: /comm/bldAttendant/getJsonData.cmd (AJAX POST).
#       Requires a session cookie set by visiting an HTML chart page first
#       (POST without prior GET returns "LOGOUT"). Implementing the cookie
#       flow is the right primary backend; deferred to a follow-up wave.
#   - Naver Finance
#       https://finance.naver.com/sise/sise_index_day.naver?code=VKOSPI
#       Returns paginated HTML with daily VKOSPI close. Workable as a
#       fallback; HTML is iframe-loaded so the parser needs special care.
#
# Design — pluggable fetcher:
#   - VkospiClient.set_history_provider(fn) accepts an async callable
#     returning tuple[VkospiBar, ...] for a (start, end) range.
#   - Default provider (no setter call) raises VkospiDataError so the
#     expert skips cleanly with a docs/VKOSPI_SETUP.md pointer.
#   - Tests + hindcast harnesses inject synthetic / cached providers.
#   - Live KRX or Naver implementations land in a follow-up PR without
#     touching the expert or calibration wiring.
#
# Mirrors the stub pattern used by kis_client (read-only paths wrapped,
# order endpoints intentionally NOT implemented per INV-GS-101) and
# dart_client (graceful skip when API key missing per INV-GS-107).

log: Final = structlog.get_logger(__name__)

_LIVE_FETCH_PENDING_HINT: Final[str] = (
    "VKOSPI live fetcher deferred — call client.set_history_provider(fn) to "
    "inject a backend (KRX session-cookie flow or Naver scrape) before use. "
    "See docs/VKOSPI_SETUP.md (planned)."
)


class VkospiDataError(GlostatError):
    """Raised when the VKOSPI history provider is unavailable or empty."""


@dataclass(frozen=True, slots=True)
class VkospiBar:
    bar_date: date
    close: float

    def __post_init__(self) -> None:
        if self.close < 0:
            raise ValueError(f"VKOSPI close must be non-negative, got {self.close}")


@dataclass(frozen=True, slots=True)
class VkospiDelta:
    """Same-day VKOSPI change at a candidate event date.

    Sign of `pct_change` is the canonical signal:
      pct_change > 0  → fear regime (VKOSPI↑ — Whaley 2009 fear gauge)
      pct_change < 0  → calm regime (VKOSPI↓)
    """

    bar_date: date
    close_t: float
    close_t_minus_1: float
    abs_change: float
    pct_change: float

    @property
    def fear_regime(self) -> bool:
        return self.pct_change > 0.0

    @property
    def calm_regime(self) -> bool:
        return self.pct_change < 0.0


HistoryProvider = Callable[[date, date], Awaitable[tuple[VkospiBar, ...]]]


def _default_provider_unavailable(_start: date, _end: date) -> Awaitable[tuple[VkospiBar, ...]]:
    raise VkospiDataError(_LIVE_FETCH_PENDING_HINT)


class VkospiClient:
    """Fetch VKOSPI daily history via a pluggable provider.

    Skip semantics: provider failures raise `VkospiDataError`; the expert
    catches and surfaces an `ExpertSkipError` so the composite predictor
    sees a clean skip instead of a crash.
    """

    def __init__(
        self,
        *,
        snapshot_broker: SnapshotBroker | None = None,
        history_provider: HistoryProvider | None = None,
    ) -> None:
        self._broker = snapshot_broker
        self._history_provider: HistoryProvider | None = history_provider
        self._cache: dict[tuple[date, date], tuple[VkospiBar, ...]] = {}
        self._last_snapshot_id: str | None = None

    @property
    def last_snapshot_id(self) -> str | None:
        return self._last_snapshot_id

    def set_history_provider(self, provider: HistoryProvider) -> None:
        # WHY: explicit setter (rather than making provider mandatory at init)
        # so the same VkospiClient instance can swap backends in tests + live
        # mode without rebuilding the calibration / snapshot wiring.
        self._history_provider = provider

    async def get_history(
        self, *, start: date, end: date,
    ) -> tuple[VkospiBar, ...]:
        if end < start:
            raise ValueError(
                f"VKOSPI history end {end} < start {start}"
            )
        cache_key = (start, end)
        if cache_key in self._cache:
            return self._cache[cache_key]
        provider = self._history_provider
        if provider is None:
            raise VkospiDataError(_LIVE_FETCH_PENDING_HINT)
        try:
            bars = await provider(start, end)
        except VkospiDataError:
            raise
        except Exception as exc:
            raise VkospiDataError(
                f"VKOSPI provider failed for {start}..{end}: {exc}"
            ) from exc
        if not bars:
            raise VkospiDataError(
                f"VKOSPI provider returned no bars for {start}..{end}"
            )
        # Sort + freeze.
        sorted_bars = tuple(sorted(bars, key=lambda b: b.bar_date))
        self._cache[cache_key] = sorted_bars
        self._record_snapshot(start, end, sorted_bars)
        return sorted_bars

    async def get_delta_at(
        self, day: date, *, lookback_days: int = 30,
    ) -> VkospiDelta:
        # WHY: ΔVKOSPI on the event day = close(day) − close(prev trading day).
        # We fetch a 30d window around `day` to absorb weekends/holidays and
        # let the parser pick the most-recent prior trading day.
        start = day - timedelta(days=lookback_days)
        end = day
        bars = await self.get_history(start=start, end=end)
        return compute_delta_at(bars, day)

    def _record_snapshot(
        self, start: date, end: date, bars: tuple[VkospiBar, ...],
    ) -> None:
        if self._broker is None or not bars:
            return
        try:
            params = {
                "start": start.isoformat(), "end": end.isoformat(),
                "n_bars": len(bars),
            }
            key = SnapshotKey(
                uaid="XKRX.VKOSPI",
                edge_type="vkospi_history",
                ts_utc=datetime.now(tz=UTC),
                tool="vkospi_client.history",
                params_canon=json.dumps(params, sort_keys=True, separators=(",", ":")),
            )
            payload = {
                "first_date": bars[0].bar_date.isoformat(),
                "last_date": bars[-1].bar_date.isoformat(),
                "n_bars": len(bars),
                "last_close": bars[-1].close,
            }
            record = self._broker.save_snapshot(key, payload)
            self._last_snapshot_id = record.leaf.leaf_hash
        except Exception as exc:
            log.warning("vkospi.snapshot_failed", err=str(exc))


def compute_delta_at(
    bars: tuple[VkospiBar, ...], day: date,
) -> VkospiDelta:
    """Pure helper — pick (close_t, close_t-1) from a sorted bar series.

    Raises VkospiDataError if `day` is not in the series or there is no
    prior bar to compute Δ. Used directly by tests + the expert wrapper
    so the network call is always optional.
    """
    if not bars:
        raise VkospiDataError("compute_delta_at: empty VKOSPI series")
    # Find the bar on `day`.
    on_day: VkospiBar | None = None
    prior: VkospiBar | None = None
    for b in bars:
        if b.bar_date == day:
            on_day = b
            break
        if b.bar_date < day:
            prior = b
    if on_day is None:
        # `day` not present — fall back to the last bar on/before day if any.
        candidate: VkospiBar | None = None
        for b in bars:
            if b.bar_date <= day and (candidate is None or b.bar_date > candidate.bar_date):
                candidate = b
        if candidate is None:
            raise VkospiDataError(
                f"compute_delta_at: no VKOSPI bar on/before {day.isoformat()}"
            )
        on_day = candidate
    # Re-locate prior bar (the closest preceding entry to on_day).
    prior = None
    for b in bars:
        if b.bar_date < on_day.bar_date and (
            prior is None or b.bar_date > prior.bar_date
        ):
            prior = b
    if prior is None:
        raise VkospiDataError(
            f"compute_delta_at: no prior VKOSPI bar before {on_day.bar_date}"
        )
    abs_change = on_day.close - prior.close
    pct_change = abs_change / prior.close if prior.close > 0 else 0.0
    return VkospiDelta(
        bar_date=on_day.bar_date,
        close_t=on_day.close,
        close_t_minus_1=prior.close,
        abs_change=abs_change,
        pct_change=pct_change,
    )


__all__ = [
    "HistoryProvider",
    "VkospiBar",
    "VkospiClient",
    "VkospiDataError",
    "VkospiDelta",
    "compute_delta_at",
]
