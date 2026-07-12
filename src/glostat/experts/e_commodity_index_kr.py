from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Final

import structlog

from glostat.core.errors import ExpertSkipError
from glostat.core.types import ExpertSignal
from glostat.data.commodity_client import (
    CommodityClient,
    CommodityCycle,
    CommodityDataError,
    CommodityKey,
    CrackSpread,
)
from glostat.data.data_router import normalize_kr_ticker
from glostat.data.sector_classifier_kr import is_refining

# v1.5 P6 — KR refining commodity-index expert.
#
# WHY: P6 panel: "정유 cycle 분석에 필수적인 missing signals: crack spread,
# OPEC 정책, WTI 모멘텀". E_FUNDAMENTAL_KR_CYCLICAL absorbs the trough/peak
# percentile; this expert provides the *direction of travel* via 30-day
# momentum on WTI + crack spread.
#
# Universe gate: refining tickers only (정유주). Other cyclical sectors
# already get cycle-direction info via E_FUNDAMENTAL_KR_CYCLICAL's commodity
# overlay; the dedicated commodity-momentum expert is reserved for refiners
# where margin pass-through is most direct.
#
# Score formula:
#   wti_signal   = clamp(wti_momentum_30d * 5.0,    -1.5, +1.5)
#   crack_signal = clamp(crack_momentum_30d * 5.0,  -1.5, +1.5)
#   raw_score    = 0.5 * wti_signal + 0.5 * crack_signal
#   net_score    = clip(raw_score, ±SCORE_CLIP)

log: Final = structlog.get_logger(__name__)

_MOMENTUM_GAIN: Final[float] = 5.0       # +20% momentum → +1.0 sub-signal
_SUB_SIGNAL_CLIP: Final[float] = 1.5
_SCORE_CLIP: Final[float] = 2.0
# WHY: commodity-momentum is one of two signals fed to refining-only universe;
# keep threshold sensitive so a +10% WTI move + small crack uptick fires LONG.
_DIRECTION_THRESHOLD: Final[float] = 0.3
_SWING_HORIZON_DAYS: Final[int] = 30


@dataclass(frozen=True, slots=True)
class CommodityIndexScore:
    wti_momentum: float
    crack_momentum: float
    wti_signal: float
    crack_signal: float
    raw_score: float
    net_score: float

    @property
    def direction(self) -> str:
        if self.net_score > _DIRECTION_THRESHOLD:
            return "LONG"
        if self.net_score < -_DIRECTION_THRESHOLD:
            return "SHORT"
        return "NEUTRAL"

    @property
    def confidence(self) -> float:
        return min(1.0, abs(self.net_score) / _SCORE_CLIP)


@dataclass(frozen=True, slots=True)
class _Source:
    name: str
    snapshot_id: str


class ECommodityIndexKrExpert:
    """KR refining-sector commodity-momentum signal (WTI + crack spread).

    Activates only on refining tickers (sector_classifier_kr.is_refining).
    Falls back to ExpertSkipError otherwise so the composite predictor doesn't
    apply oil-price momentum to non-oil-sensitive names.
    """

    name = "E_COMMODITY_INDEX_KR"

    def __init__(self, *, commodity_client: CommodityClient) -> None:
        self._commodity = commodity_client

    async def compute(self, ticker: str, ts: datetime) -> ExpertSignal:
        code = normalize_kr_ticker(ticker)
        if not is_refining(code):
            raise ExpertSkipError(
                f"E_COMMODITY_INDEX_KR: {code} not in KR refining universe"
            )
        sources: list[_Source] = []
        wti = await self._fetch_wti(sources)
        crack = await self._fetch_crack()
        score = _score(wti, crack)
        return _build_signal(
            code=code, ts=ts, score=score,
            wti=wti, crack=crack, sources=sources,
        )

    async def _fetch_wti(self, sources: list[_Source]) -> CommodityCycle:
        try:
            cycle = await self._commodity.get_cycle(CommodityKey.WTI)
        except CommodityDataError as exc:
            raise ExpertSkipError(
                f"E_COMMODITY_INDEX_KR: WTI fetch failed: {exc}"
            ) from exc
        if cycle.snapshot_id is not None:
            sources.append(_Source(
                name="commodity.wti", snapshot_id=cycle.snapshot_id,
            ))
        return cycle

    async def _fetch_crack(self) -> CrackSpread:
        try:
            return await self._commodity.get_crack_spread()
        except CommodityDataError as exc:
            raise ExpertSkipError(
                f"E_COMMODITY_INDEX_KR: crack spread fetch failed: {exc}"
            ) from exc


def _score(wti: CommodityCycle, crack: CrackSpread) -> CommodityIndexScore:
    wti_signal = max(-_SUB_SIGNAL_CLIP, min(_SUB_SIGNAL_CLIP,
                                            wti.momentum_30d * _MOMENTUM_GAIN))
    crack_signal = max(-_SUB_SIGNAL_CLIP, min(_SUB_SIGNAL_CLIP,
                                              crack.momentum_30d * _MOMENTUM_GAIN))
    raw = 0.5 * wti_signal + 0.5 * crack_signal
    net = max(-_SCORE_CLIP, min(_SCORE_CLIP, raw))
    return CommodityIndexScore(
        wti_momentum=wti.momentum_30d,
        crack_momentum=crack.momentum_30d,
        wti_signal=wti_signal,
        crack_signal=crack_signal,
        raw_score=raw,
        net_score=net,
    )


def _build_signal(
    *,
    code: str,
    ts: datetime,
    score: CommodityIndexScore,
    wti: CommodityCycle,
    crack: CrackSpread,
    sources: list[_Source],
) -> ExpertSignal:
    basis = (
        f"WTI ${wti.last_close:.1f}/bbl 30d_mom={wti.momentum_30d:+.2%}, "
        f"crack ${crack.last_spread:.1f}/bbl 30d_mom={crack.momentum_30d:+.2%}, "
        f"net={score.net_score:+.2f}"
    )
    metadata = tuple(sorted({
        "wti_last_close": f"{wti.last_close:.4f}",
        "wti_momentum_30d": f"{wti.momentum_30d:.6f}",
        "wti_pctile": f"{wti.cycle_percentile:.4f}",
        "crack_last_spread": f"{crack.last_spread:.4f}",
        "crack_momentum_30d": f"{crack.momentum_30d:.6f}",
        "crack_pctile": f"{crack.cycle_percentile:.4f}",
        "wti_signal": f"{score.wti_signal:.4f}",
        "crack_signal": f"{score.crack_signal:.4f}",
        "raw_score": f"{score.raw_score:.4f}",
        "net_score": f"{score.net_score:.4f}",
        "code": code,
    }.items()))
    source_strings: tuple[str, ...] = tuple(
        f"{s.name}#{s.snapshot_id[:12]}" for s in sources
    ) or ("e_commodity_index_kr.synthetic",)
    return ExpertSignal(
        expert_name="E_COMMODITY_INDEX_KR",  # type: ignore[arg-type]
        ticker=code,
        direction=score.direction,  # type: ignore[arg-type]
        net_score=score.net_score,
        confidence=score.confidence,
        archetype="continuation",   # momentum-following, not contrarian
        basis=basis,
        sources=source_strings,
        expires_at=ts + timedelta(days=_SWING_HORIZON_DAYS),
        metadata=metadata,
    )


__all__ = [
    "CommodityIndexScore",
    "ECommodityIndexKrExpert",
]
