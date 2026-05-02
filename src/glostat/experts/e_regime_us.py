from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Final

import structlog

from glostat.core.errors import ExpertSkipError
from glostat.core.types import ExpertSignal
from glostat.data.data_router import is_kr_ticker
from glostat.data.regime_us_client import (
    RegimeDataError,
    RegimeUsClient,
    UstCurveSlope,
    VixTermStructure,
)

# v1.10 — US regime expert (VIX term structure + UST curve slope).
#
# WHY: Closes the documented US peer of E_MACRO_KR. Two well-known regime
# signals fold into a single net score in [-3, +3]:
#
#   vix_term_term:   ratio = VIX9D / VIX3M
#                    contango (ratio < 1)  →  bullish equity drift  (+1 weight)
#                    backwardation (>= 1)  →  bearish (vol shock priced in)
#
#   curve_slope:     bps = (10y - 3m) * 100
#                    inverted (< 0)        →  bearish (recession signal)
#                    steep   (> 100bps)    →  bullish (recovery / late cycle)
#
# Skip cleanly on:
#   - KR tickers (use E_MACRO_KR instead)
#   - Crypto / FX / commodity ETFs (no SPX-tied regime mapping)
#   - Any series fetch failure (yfinance flaky on ^VIX9D / ^VIX3M historicals)
#
# n=0 bootstrap. Calibration weight = 0 until a dedicated US-regime hindcast
# measures predictive AUC (deferred to wave-2 follow-up). Surfaces in
# contributing_signals with raw_score + basis so the user sees the regime
# picture even when weight=0.

log: Final = structlog.get_logger(__name__)

_WEIGHT_VIX_TERM: Final[float] = 1.0
_WEIGHT_CURVE: Final[float] = 1.0
_SCORE_CLIP: Final[float] = 3.0
_DIRECTION_THRESHOLD: Final[float] = 0.6
_HORIZON_DAYS: Final[int] = 30

# Calibration anchors derived from public regime literature:
# VIX term ratio of 1.0 is the contango/backwardation boundary; ratio of 0.85
# is "deeply contango" (calm market) → +1 z. Ratio of 1.15 is "deeply
# backwardated" (acute stress) → -2 z. Linear in between.
_VIX_RATIO_NEUTRAL: Final[float] = 1.0
_VIX_RATIO_SCALE: Final[float] = 0.10        # one z-unit = 10% deviation

# Curve: 0 bps is the inversion line. 100 bps is mid-cycle steepness.
# We score the inversion (-) bearishly and steepness (+) bullishly with
# a soft cap so 300bps doesn't dominate.
_CURVE_NEUTRAL_BPS: Final[float] = 0.0
_CURVE_SCALE_BPS: Final[float] = 100.0       # one z-unit = 100 bps


@dataclass(frozen=True, slots=True)
class RegimeUsInputs:
    vix_term: VixTermStructure | None
    curve: UstCurveSlope | None


@dataclass(frozen=True, slots=True)
class RegimeUsScore:
    vix_term_term: float
    curve_term: float
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

    @property
    def clipped(self) -> bool:
        return abs(self.raw_score) > _SCORE_CLIP


@dataclass(frozen=True, slots=True)
class _Source:
    name: str
    snapshot_id: str | None


class ERegimeUsExpert:
    """US regime expert — VIX term structure + UST 3m-10y curve slope.

    Universe: US tickers only (skip KR, skip non-equity proxies). Bootstraps at
    n=0 in the calibration table; weight=0 in composite until a dedicated
    US-regime hindcast measures real AUC.
    """

    name = "E_REGIME_US"

    def __init__(self, *, regime_client: RegimeUsClient) -> None:
        self._regime = regime_client

    async def compute(self, ticker: str, ts: datetime) -> ExpertSignal:
        if is_kr_ticker(ticker):
            raise ExpertSkipError(
                f"E_REGIME_US: ticker {ticker!r} is KR; use E_MACRO_KR instead"
            )
        as_of = ts.date()
        sources: list[_Source] = []
        inputs = await self._fetch_inputs(as_of, sources)
        if inputs.vix_term is None and inputs.curve is None:
            raise ExpertSkipError(
                f"E_REGIME_US: no usable VIX or UST series for "
                f"{as_of.isoformat()}"
            )
        score = score_regime_us(inputs)
        return _build_signal(
            ticker=ticker.upper(), ts=ts, inputs=inputs,
            score=score, sources=sources,
        )

    async def _fetch_inputs(
        self, as_of, sources: list[_Source],
    ) -> RegimeUsInputs:
        vix = await self._safe_vix(as_of, sources)
        curve = await self._safe_curve(as_of, sources)
        return RegimeUsInputs(vix_term=vix, curve=curve)

    async def _safe_vix(
        self, as_of, sources: list[_Source],
    ) -> VixTermStructure | None:
        try:
            v = await self._regime.get_vix_term(as_of=as_of)
        except RegimeDataError as exc:
            log.info("e_regime_us.vix_skip", err=str(exc))
            return None
        sources.append(_Source(
            name="regime_us.vix_term",
            snapshot_id=getattr(self._regime._yf, "last_snapshot_id", None),
        ))
        return v

    async def _safe_curve(
        self, as_of, sources: list[_Source],
    ) -> UstCurveSlope | None:
        try:
            c = await self._regime.get_curve_slope(as_of=as_of)
        except RegimeDataError as exc:
            log.info("e_regime_us.curve_skip", err=str(exc))
            return None
        sources.append(_Source(
            name="regime_us.curve_slope",
            snapshot_id=getattr(self._regime._yf, "last_snapshot_id", None),
        ))
        return c


# ── pure scoring (testable without network) ────────────────────────────────


def score_regime_us(inputs: RegimeUsInputs) -> RegimeUsScore:
    vix_term_term = _vix_term_score(inputs.vix_term)
    curve_term = _curve_score(inputs.curve)
    raw = vix_term_term + curve_term
    net = max(-_SCORE_CLIP, min(_SCORE_CLIP, raw))
    return RegimeUsScore(
        vix_term_term=vix_term_term,
        curve_term=curve_term,
        raw_score=raw,
        net_score=net,
    )


def _vix_term_score(v: VixTermStructure | None) -> float:
    if v is None:
        return 0.0
    # Contango (ratio < 1) → bullish; backwardation (>= 1) → bearish.
    # invert sign so positive z-deviation (ratio > neutral) yields negative term.
    z = (_VIX_RATIO_NEUTRAL - v.ratio) / _VIX_RATIO_SCALE
    z = max(-2.0, min(2.0, z))
    return _WEIGHT_VIX_TERM * z


def _curve_score(c: UstCurveSlope | None) -> float:
    if c is None:
        return 0.0
    # Inversion (slope < 0) → bearish; steepness → bullish.
    z = (c.slope_bps - _CURVE_NEUTRAL_BPS) / _CURVE_SCALE_BPS
    z = max(-2.0, min(2.0, z))
    return _WEIGHT_CURVE * z


# ── signal builder ─────────────────────────────────────────────────────────


def _build_signal(
    *,
    ticker: str,
    ts: datetime,
    inputs: RegimeUsInputs,
    score: RegimeUsScore,
    sources: list[_Source],
) -> ExpertSignal:
    vix = inputs.vix_term
    curve = inputs.curve
    parts: list[str] = []
    if vix is not None:
        parts.append(
            f"VIX term ratio={vix.ratio:.3f} "
            f"({'BACKWARDATED' if vix.in_backwardation else 'contango'})"
        )
    if curve is not None:
        parts.append(
            f"UST 3m-10y={curve.slope_bps:+.0f}bps "
            f"({'INVERTED' if curve.inverted else 'positive'})"
        )
    basis = " · ".join(parts) if parts else "no regime data"
    metadata_dict: dict[str, str] = {
        "vix_term_term": f"{score.vix_term_term:.4f}",
        "curve_term": f"{score.curve_term:.4f}",
        "raw_score": f"{score.raw_score:.4f}",
        "net_score": f"{score.net_score:.4f}",
        "clipped": str(score.clipped),
    }
    if vix is not None:
        metadata_dict["vix9d"] = f"{vix.vix9d:.4f}"
        metadata_dict["vix3m"] = f"{vix.vix3m:.4f}"
        metadata_dict["vix_ratio"] = f"{vix.ratio:.4f}"
        metadata_dict["vix_backwardation"] = str(vix.in_backwardation)
    if curve is not None:
        metadata_dict["curve_front_pct"] = f"{curve.front_yield_pct:.4f}"
        metadata_dict["curve_back_pct"] = f"{curve.back_yield_pct:.4f}"
        metadata_dict["curve_slope_bps"] = f"{curve.slope_bps:.4f}"
        metadata_dict["curve_inverted"] = str(curve.inverted)
    metadata = tuple(sorted(metadata_dict.items()))
    source_strings: tuple[str, ...] = tuple(
        f"{s.name}#{(s.snapshot_id or 'nosnap')[:12]}" for s in sources
    ) or ("e_regime_us.synthetic",)
    return ExpertSignal(
        expert_name="E_REGIME_US",  # type: ignore[arg-type]
        ticker=ticker,
        direction=score.direction,  # type: ignore[arg-type]
        net_score=score.net_score,
        confidence=score.confidence,
        archetype="continuation",
        basis=basis,
        sources=source_strings,
        expires_at=ts.replace(tzinfo=ts.tzinfo or UTC) + timedelta(days=_HORIZON_DAYS),
        metadata=metadata,
    )


__all__ = [
    "ERegimeUsExpert",
    "RegimeUsInputs",
    "RegimeUsScore",
    "score_regime_us",
]
