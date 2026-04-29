from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Final

import structlog

from glostat.core.errors import ExpertSkipError
from glostat.core.types import ExpertSignal
from glostat.data.data_router import DataRouter, normalize_kr_ticker, to_yfinance_kr_ticker
from glostat.data.yfinance_types import Fundamentals

# v1.1 K1 — KR-specific E_FUNDAMENTAL.
#
# WHY: the US E_FUNDAMENTAL pulls SEC EDGAR XBRL company facts; KR has no
# equivalent free XBRL feed. yfinance partially covers KR fundamentals when
# the ticker carries .KS / .KQ suffix — PER, dividend yield, market cap, beta.
# We score with the same per_z + roe_z + dividend_yield approach used in the
# US expert but with KR-specific defaults (KOSPI 200 megacaps tend to trade
# at lower PER than S&P 500 due to chaebol discount + lower ROE typical of
# manufacturing-heavy mix). Sector-aware z-score deferred to v1.2 once a KR
# sector resolver lands.

log: Final = structlog.get_logger(__name__)

# Conservative KOSPI 200 historical medians (2024-01 .. 2026-04 snapshot).
# Sourced from KRX market-statistics monthly publications (snapshot 2026-04-29);
# refreshed quarterly with the universe.
_KR_PER_MEDIAN: Final[float] = 11.5
_KR_PER_STDDEV: Final[float] = 6.0
_KR_ROE_MEDIAN: Final[float] = 0.085
_KR_ROE_STDDEV: Final[float] = 0.045
_KR_DIV_YIELD_MEDIAN: Final[float] = 0.018  # 1.8% — KR megacap median

_WEIGHT_PER: Final[float] = 0.45     # value tilt — per is the dominant KR signal
_WEIGHT_ROE: Final[float] = 0.40     # quality
_WEIGHT_DIV: Final[float] = 0.15     # income / capital allocation discipline

_DIRECTION_THRESHOLD: Final[float] = 1.0   # KR is noisier; relax 1.5 → 1.0
_SCORE_CLIP: Final[float] = 3.0
_SWING_HORIZON_DAYS: Final[int] = 30


@dataclass(frozen=True, slots=True)
class FundamentalKrScore:
    per_z: float
    roe_z: float
    div_z: float
    net_score: float
    raw_score: float = 0.0

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

    @property
    def clipped(self) -> bool:
        return abs(self.raw_score) > _SCORE_CLIP


@dataclass(frozen=True, slots=True)
class _Source:
    name: str
    snapshot_id: str
    ts: datetime


class EFundamentalKrExpert:
    """KR-specific E_FUNDAMENTAL using yfinance .KS/.KQ data only.

    No SEC EDGAR (KR not in scope). Future Phase 2.5: integrate DART API for
    XBRL-grade financial-statement signals (revenue/NI trend).
    """

    name = "E_FUNDAMENTAL_KR"

    def __init__(self, *, router: DataRouter) -> None:
        self._router = router

    async def compute(self, ticker: str, ts: datetime) -> ExpertSignal:
        code = normalize_kr_ticker(ticker)
        sources: list[_Source] = []
        fundamentals = await self._fetch_fundamentals(code, sources)
        # Fail-fast: yfinance KR fundamentals are partial; PER absence + no ROE
        # means the symbol is illiquid or yfinance has stale info. Surface the
        # skip honestly so the composite doesn't dilute.
        if fundamentals.pe_ratio is None and fundamentals.roe is None:
            raise ExpertSkipError(
                f"E_FUNDAMENTAL_KR: missing PER and ROE for {code}@{ts.date().isoformat()}"
            )
        score = _score_kr(fundamentals)
        return _build_signal(code=code, ts=ts, score=score, fundamentals=fundamentals,
                             sources=sources)

    async def _fetch_fundamentals(self, code: str, sources: list[_Source]) -> Fundamentals:
        client, method = self._router.route(self.name, "fundamentals")
        # KR codes need .KS suffix for yfinance lookup (INV-GS-106).
        yf_ticker = to_yfinance_kr_ticker(code)
        try:
            result: Fundamentals = await getattr(client, method)(yf_ticker)
        except Exception as exc:
            raise ExpertSkipError(
                f"E_FUNDAMENTAL_KR: yfinance failed for {yf_ticker}: {exc}"
            ) from exc
        snap_id = getattr(client, "last_snapshot_id", None)
        if snap_id is not None:
            sources.append(
                _Source(
                    name="yfinance.info.kr",
                    snapshot_id=snap_id,
                    ts=datetime.now(tz=UTC),
                )
            )
        return result


def _score_kr(f: Fundamentals) -> FundamentalKrScore:
    per_z = _per_z(f.pe_ratio)
    roe_z = _roe_z(f.roe)
    div_z = _div_z(f.dividend_yield)
    # WHY: lower PER → positive value tilt → invert per_z so cheap = +.
    per_signal = -per_z
    raw = (
        _WEIGHT_PER * per_signal
        + _WEIGHT_ROE * roe_z
        + _WEIGHT_DIV * div_z
    )
    net = max(-_SCORE_CLIP, min(_SCORE_CLIP, raw))
    return FundamentalKrScore(
        per_z=per_signal, roe_z=roe_z, div_z=div_z,
        net_score=net, raw_score=raw,
    )


def _per_z(per: float | None) -> float:
    if per is None or per <= 0:
        return 0.0
    return (per - _KR_PER_MEDIAN) / max(_KR_PER_STDDEV, 1e-3)


def _roe_z(roe: float | None) -> float:
    if roe is None:
        return 0.0
    return (roe - _KR_ROE_MEDIAN) / max(_KR_ROE_STDDEV, 1e-3)


def _div_z(yield_: float | None) -> float:
    if yield_ is None:
        return 0.0
    # +/- around median; cap at ±2.0 to avoid one fat dividend dominating.
    delta = (yield_ - _KR_DIV_YIELD_MEDIAN) / max(_KR_DIV_YIELD_MEDIAN, 1e-3)
    return max(-2.0, min(2.0, delta))


def _build_signal(
    *,
    code: str,
    ts: datetime,
    score: FundamentalKrScore,
    fundamentals: Fundamentals,
    sources: list[_Source],
) -> ExpertSignal:
    basis = (
        f"PER {fundamentals.pe_ratio} z={score.per_z:.2f}, "
        f"ROE {fundamentals.roe} z={score.roe_z:.2f}, "
        f"div_yield {fundamentals.dividend_yield} z={score.div_z:.2f} (KR median)"
    )
    metadata: tuple[tuple[str, str], ...] = tuple(
        sorted(
            {
                "per_raw": _fmt(fundamentals.pe_ratio),
                "roe_raw": _fmt(fundamentals.roe),
                "div_yield_raw": _fmt(fundamentals.dividend_yield),
                "market_cap": _fmt(fundamentals.market_cap),
                "per_z": f"{score.per_z:.4f}",
                "roe_z": f"{score.roe_z:.4f}",
                "div_z": f"{score.div_z:.4f}",
                "net_score": f"{score.net_score:.4f}",
                "raw_score": f"{score.raw_score:.4f}",
                "clipped": str(score.clipped),
                "weight_per": f"{_WEIGHT_PER}",
                "weight_roe": f"{_WEIGHT_ROE}",
                "weight_div": f"{_WEIGHT_DIV}",
                "kr_per_median": f"{_KR_PER_MEDIAN}",
                "kr_roe_median": f"{_KR_ROE_MEDIAN}",
                "kr_div_yield_median": f"{_KR_DIV_YIELD_MEDIAN}",
                "code": code,
            }.items()
        )
    )
    source_strings: tuple[str, ...] = tuple(
        f"{s.name}#{s.snapshot_id[:12]}" for s in sources
    ) or ("e_fundamental_kr.synthetic",)
    return ExpertSignal(
        expert_name="E_FUNDAMENTAL_KR",  # type: ignore[arg-type]
        ticker=code,
        direction=score.direction,  # type: ignore[arg-type]
        net_score=score.net_score,
        confidence=score.confidence,
        archetype="continuation",
        basis=basis,
        sources=source_strings,
        expires_at=ts + timedelta(days=_SWING_HORIZON_DAYS),
        metadata=metadata,
    )


def _fmt(v: float | None) -> str:
    return "n/a" if v is None else str(v)


__all__ = [
    "EFundamentalKrExpert",
    "FundamentalKrScore",
]
