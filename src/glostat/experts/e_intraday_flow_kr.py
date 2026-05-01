from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Final

import structlog

from glostat.core.errors import ExpertSkipError
from glostat.core.types import ExpertSignal
from glostat.data.data_router import is_kr_ticker, normalize_kr_ticker
from glostat.data.kis_client import KisApiError, KisClient, KisIntradayFlow
from glostat.data.naver_kr_client import KrFlowBar, NaverKrClient

# v1.4 N2 — KR intraday investor-flow expert. Inspired by TITAN E5+
# (intraday_flow.py), reframed as a free-stack prediction signal.
#
# Signal logic:
#   - Compute per-day cumulative flow from Naver bars (most recent N days).
#   - Acceleration (2nd derivative) above threshold → momentum signal.
#   - Foreign flow leading retail/organ flow → strong directional bias.
#   - When KIS is wired, latest intraday snapshot adds a "today" sample to the
#     trailing series so the acceleration term reflects intraday movement.
#
# Universe: KOSPI 200 (high liquidity required for the signal to mean anything).
# Skip cleanly when intraday data missing (most pre-IPO / illiquid names).

log: Final = structlog.get_logger(__name__)

_LOOKBACK_DAYS: Final[int] = 5
_ACCEL_THRESHOLD: Final[float] = 0.30  # 30% Δ in flow rate
_LEAD_THRESHOLD: Final[float] = 0.50   # foreign flow >= 50% of organ in same direction
_SCORE_PER_COMPONENT: Final[float] = 0.5
_SCORE_CLIP: Final[float] = 3.0
_DIRECTION_THRESHOLD: Final[float] = 0.5
_HORIZON_DAYS: Final[int] = 5


@dataclass(frozen=True, slots=True)
class IntradayFlowScore:
    code: str
    foreign_recent_avg: float
    foreign_acceleration: float        # rate-of-change in flow rate
    organ_recent_avg: float
    foreign_leads_organ: bool
    raw_score: float
    net_score: float
    direction: str                     # LONG / SHORT / NEUTRAL
    signal: str                        # FLOW_IMPROVING / FLOW_DETERIORATING / NEUTRAL
    sources: tuple[str, ...]

    @property
    def confidence(self) -> float:
        return min(1.0, abs(self.net_score) / _SCORE_CLIP)


def score_intraday_flow(
    *,
    code: str,
    naver_bars: Sequence[KrFlowBar],
    kis_intraday: KisIntradayFlow | None = None,
    lookback_days: int = _LOOKBACK_DAYS,
) -> IntradayFlowScore:
    if not naver_bars:
        return _neutral(code, sources=())
    bars_sorted = sorted(naver_bars, key=lambda b: b.bar_date)
    recent = bars_sorted[-lookback_days:]
    if len(recent) < 2:
        return _neutral(code, sources=("naver",))
    foreign_avg = sum(b.foreign_net for b in recent) / len(recent)
    organ_avg = sum(b.organ_net for b in recent) / len(recent)
    # Acceleration: compare the most-recent half to the prior half. Optionally
    # promote the KIS intraday sample (today's running total) into the recent
    # window so today's movement counts.
    sources: list[str] = ["naver"]
    foreign_today: float | None = None
    if kis_intraday is not None:
        sources.append("kis")
        foreign_today = kis_intraday.foreign_net
    accel = _flow_acceleration(
        [b.foreign_net for b in recent], today=foreign_today,
    )
    leads_organ = (
        organ_avg != 0.0 and (
            (foreign_avg > 0 and foreign_avg / max(abs(organ_avg), 1.0) > _LEAD_THRESHOLD)
            or (foreign_avg < 0 and abs(foreign_avg) / max(abs(organ_avg), 1.0) > _LEAD_THRESHOLD)
        )
    )
    raw = 0.0
    signal = "NEUTRAL"
    # 1) Foreign flow direction × magnitude
    if foreign_avg > 0:
        raw += _SCORE_PER_COMPONENT
        signal = "FLOW_IMPROVING"
    elif foreign_avg < 0:
        raw -= _SCORE_PER_COMPONENT
        signal = "FLOW_DETERIORATING"
    # 2) Acceleration above threshold strengthens the signal
    if abs(accel) > _ACCEL_THRESHOLD:
        raw += _SCORE_PER_COMPONENT * (1.0 if accel > 0 else -1.0)
    # 3) Foreign leading organ → confirmation boost
    if leads_organ:
        raw += _SCORE_PER_COMPONENT * (1.0 if foreign_avg > 0 else -1.0)
    net = max(-_SCORE_CLIP, min(_SCORE_CLIP, raw))
    direction = (
        "LONG" if net > _DIRECTION_THRESHOLD
        else "SHORT" if net < -_DIRECTION_THRESHOLD
        else "NEUTRAL"
    )
    return IntradayFlowScore(
        code=code, foreign_recent_avg=foreign_avg,
        foreign_acceleration=accel, organ_recent_avg=organ_avg,
        foreign_leads_organ=leads_organ, raw_score=raw, net_score=net,
        direction=direction, signal=signal, sources=tuple(sources),
    )


def _flow_acceleration(values: list[float], *, today: float | None = None) -> float:
    series = list(values)
    if today is not None:
        series.append(today)
    if len(series) < 2:
        return 0.0
    half = max(1, len(series) // 2)
    earlier = series[:half]
    later = series[half:]
    e_avg = sum(earlier) / len(earlier)
    l_avg = sum(later) / len(later)
    denom = max(abs(e_avg), abs(l_avg), 1.0)
    return (l_avg - e_avg) / denom


def _neutral(code: str, *, sources: tuple[str, ...]) -> IntradayFlowScore:
    return IntradayFlowScore(
        code=code, foreign_recent_avg=0.0, foreign_acceleration=0.0,
        organ_recent_avg=0.0, foreign_leads_organ=False,
        raw_score=0.0, net_score=0.0, direction="NEUTRAL", signal="NEUTRAL",
        sources=sources,
    )


class EIntradayFlowKrExpert:
    """KR intraday flow expert — Naver baseline + optional KIS intraday overlay.

    Skip when the KOSPI 200 universe doesn't include the ticker, or when Naver
    returns insufficient bars (signal needs at least 2 recent days).
    """

    name = "E_INTRADAY_FLOW_KR"

    def __init__(
        self,
        *,
        naver_client: NaverKrClient | None = None,
        kis_client: KisClient | None = None,
        kospi200: frozenset[str] | None = None,
        max_pages: int = 2,    # ~40 trading days; only need recent activity
    ) -> None:
        self._naver = naver_client
        self._kis = kis_client
        self._kospi200 = kospi200 or frozenset()
        self._max_pages = max_pages

    @classmethod
    def from_env(
        cls, *, kospi200: frozenset[str] | None = None,
    ) -> EIntradayFlowKrExpert | None:
        # Naver is always available; KIS is best-effort.
        naver = NaverKrClient()
        kis = None
        from glostat.data.kis_client import is_kis_configured  # noqa: PLC0415

        if is_kis_configured():
            try:
                kis = KisClient()
            except Exception as exc:
                log.info("e_intraday_flow_kr.kis_init_skip", err=str(exc))
                kis = None
        return cls(naver_client=naver, kis_client=kis, kospi200=kospi200)

    async def compute(self, ticker: str, ts: datetime) -> ExpertSignal:
        if self._naver is None:
            raise ExpertSkipError(
                "E_INTRADAY_FLOW_KR: Naver client not configured"
            )
        if not is_kr_ticker(ticker):
            raise ExpertSkipError(
                f"E_INTRADAY_FLOW_KR: ticker {ticker!r} not KR equity"
            )
        code = normalize_kr_ticker(ticker)
        if self._kospi200 and code not in self._kospi200:
            raise ExpertSkipError(
                f"E_INTRADAY_FLOW_KR: {code} not in KOSPI 200 universe"
            )
        bars = await self._fetch_naver(code)
        if len(bars) < 2:
            raise ExpertSkipError(
                f"E_INTRADAY_FLOW_KR: insufficient Naver bars ({len(bars)}) for {code}"
            )
        kis_snap: KisIntradayFlow | None = None
        if self._kis is not None:
            try:
                kis_snap = await self._kis.get_intraday_flows(code)
            except KisApiError as exc:
                log.info("e_intraday_flow_kr.kis_skip", code=code, err=str(exc))
                kis_snap = None
        score = score_intraday_flow(
            code=code, naver_bars=bars, kis_intraday=kis_snap,
        )
        snap_id = (
            self._kis.last_snapshot_id if (self._kis and kis_snap is not None)
            else f"naver_kr.{code}"
        )
        return _build_signal(code=code, ts=ts, score=score, snap_id=snap_id)

    async def _fetch_naver(self, code: str) -> list[KrFlowBar]:
        cached = self._naver.load_cached(code) if self._naver is not None else []
        if cached:
            return cached
        try:
            bars = await self._naver.fetch_history(code, max_pages=self._max_pages)
        except Exception as exc:
            log.warning("e_intraday_flow_kr.naver_failed", code=code, err=str(exc))
            return []
        if bars:
            try:
                self._naver.save_cache(code, bars)
            except Exception as exc:
                log.warning("e_intraday_flow_kr.cache_save_failed",
                            code=code, err=str(exc))
        return bars


def _build_signal(
    *, code: str, ts: datetime, score: IntradayFlowScore, snap_id: str,
) -> ExpertSignal:
    basis = (
        f"Intraday flow — signal={score.signal} foreign_avg={int(score.foreign_recent_avg):+,} "
        f"accel={score.foreign_acceleration:+.2f} leads_organ={score.foreign_leads_organ} "
        f"sources={'+'.join(score.sources)}"
    )
    metadata: tuple[tuple[str, str], ...] = tuple(
        sorted({
            "signal": score.signal,
            "foreign_recent_avg": f"{score.foreign_recent_avg:.0f}",
            "foreign_acceleration": f"{score.foreign_acceleration:.4f}",
            "organ_recent_avg": f"{score.organ_recent_avg:.0f}",
            "foreign_leads_organ": str(score.foreign_leads_organ),
            "raw_score": f"{score.raw_score:.4f}",
            "net_score": f"{score.net_score:.4f}",
            "data_sources": "+".join(score.sources),
            "code": code,
        }.items())
    )
    return ExpertSignal(
        expert_name="E_INTRADAY_FLOW_KR",  # type: ignore[arg-type]
        ticker=code,
        direction=score.direction,  # type: ignore[arg-type]
        net_score=score.net_score,
        confidence=score.confidence,
        archetype="impulse",
        basis=basis,
        sources=(snap_id,),
        expires_at=ts + timedelta(days=_HORIZON_DAYS),
        metadata=metadata,
    )


__all__ = [
    "EIntradayFlowKrExpert",
    "IntradayFlowScore",
    "score_intraday_flow",
]
