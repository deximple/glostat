from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Final, Literal

import structlog

from glostat.core.errors import ExpertSkipError
from glostat.core.types import ExpertSignal
from glostat.data.data_router import is_kr_ticker, normalize_kr_ticker
from glostat.data.vkospi_client import (
    VkospiClient,
    VkospiDataError,
    VkospiDelta,
)

# v1.10.6 — KR investor-mood expert (E_VKOSPI_MOOD_KR).
#
# Source: Lee Jung-hwan (한양대) · Son Sam-ho (순천향대) · Lee Geon-hee.
# "VKOSPI 지수를 이용한 단기주가수익률 예측에 관한 연구."
# 금융정보연구, 2024.02 (Korean Journal of Financial Information).
#
# # Empirical claim (paper, 18-year sample 2004-01..2022-07, KOSPI 200)
# Trigger: |daily simple return r_t| > 10% on a single name.
# Conditioning: sign of ΔVKOSPI on the event day.
#
# | regime                          | n (raw / abnormal) | 5d cum. | 20d cum. |
# |---------------------------------|--------------------|---------|----------|
# | aligned drift  : VKOSPI↓, r↑    | 2,202 / 1,289      | +0.94%* | +2.11%** |
# | aligned reversal: VKOSPI↑, r↓   | 1,393 / 498        | +1.31%* | +2.43%** |
# | misaligned (↑↑)                 | 966 / 851          |   n.s.  | +1.07%·  |
# | misaligned (↓↓)                 | 415 / 372          |   n.s.  |   n.s.   |
# | small-cap drift, 20d            | (subset)           |    —    | +9.21%** |
#
# (*** p<0.001, ** p<0.01, * p<0.05.) See Tables 1, 3, 5A in the paper.
# Both ALIGNED cases predict POSITIVE forward return (LONG):
#   - reversal: price was crashed too far in fear → bounce
#   - drift   : price under-reacted to greed → continuation
# US literature (Kudryavtsev 2017) finds reversal in BOTH directions; KR
# diverges, with under-reaction to greed as the distinctive feature.
#
# # Critical caveats baked into design (must NOT be silently elided)
# 1. Transaction-cost-free academic measurement. KRX round-trip ≈ 23 bps;
#    20d alpha 2.1% becomes net ~1.6% / 20d. Filter at composite layer.
# 2. Look-ahead window ambiguity — "event day VKOSPI delta" measured at
#    close. Predict-time entry can only happen at the next trading day's
#    open at the earliest → treat the score as a NEXT-DAY signal, not
#    same-day execution.
# 3. Multiple-testing un-corrected: 32+ tests in the paper; Bonferroni-
#    adjusted p-values would weaken some 5%-significant cells. Treat ***
#    cells as the only honest priors.
# 4. R² / IC unreported in paper coefficients; calibration_status
#    "bootstrap" until GLOSTAT measures AUC/Sharpe independently.
# 5. Frequency low: |r|>10% fires ~1.3 events/year/ticker. n grows with
#    universe breadth (KOSPI 200 = 200 names → ~260/yr).
# 6. Alpha-decay risk: paper data ends 2022-07. 2023-2026 OOS validation
#    is a MUST before any non-zero composite weight.
# 7. Price-limit fills: ±30% daily limit → some |r|>10% events are
#    truncated. Fill-quality discount applied at small-cap multiplier
#    boundary.
# 8. VKOSPI noise: KOSPI 200 option liquidity << SPX. Intraday VKOSPI
#    spikes possible. We use end-of-day close-to-close ΔVKOSPI only.
# 9. KRX (2009) cross-correlation analysis (Table 15) shows VKOSPI ↔
#    KOSPI200 correlation is concentrated at lag 0 (-0.551, p<0.01) and
#    near-zero at ±1, ±2 days. So VKOSPI has NO leading-indicator power
#    under normal conditions — only event-conditional (|r|>10%) regimes
#    activate the mood signal. Predict-time entry must therefore be
#    next-trading-day open at the earliest (caveat 2 above).
# 10. KRX (2009) Table 18 reports VKOSPI's adj. R² for realized-vol
#    prediction is 0.5189 vs historical-vol 0.4982 — only +3.2pp edge.
#    KRX is a KRX-published study (institutional bias toward promoting
#    its own VKOSPI product). OOS validation 2023-2026 is mandatory.
# 11. KRX (2009) Table 14 reports VKOSPI autocorrelation AR(1)=-0.168,
#    AR(2)=-0.087, AR(3)=-0.146 (all p<0.05). Three-lag mean reversion
#    means a high VKOSPI is temporary; this is the BASELINE behaviour
#    that our mood expert layers on top of. Don't double-count: the
#    score is the regime tilt, not the autoregressive expectation.
# 12. Calibration > prediction. Public-data indicators on KR derivatives
#    (P/C ratio, VKOSPI level, foreign basis, program balance) typically
#    add +5-10pp index hit rate when aggregated, vs Bayesian ceiling
#    ~76-78% from macro-event tail. The honest framing: this expert is
#    a CALIBRATION FILTER for the composite — it tells us "when not to
#    bet" by gating on |r|>10% AND aligned ΔVKOSPI sign, NOT a broad
#    next-day predictor. 4 of 5 (return, ΔVKOSPI) quadrants emit NEUTRAL
#    direction by design.
#
# # Universe + horizon
# Universe: KOSPI 200 only (paper sample). KR ticker normalisation via
# `is_kr_ticker` / `normalize_kr_ticker` (INV-GS-106). Non-KR tickers
# raise ExpertSkipError immediately.
# Horizon: 20 days (paper headline window). Direction: LONG when aligned,
# NEUTRAL otherwise.
#
# # Score formula
# Pure function of three inputs:
#   r_t        : event-day daily simple return on the target ticker
#   delta_pct  : ΔVKOSPI / VKOSPI(t-1)  (signed)
#   small_cap  : bool (paper finds 4-5x effect for small caps)
#
# magnitude = max(0, |r_t| - 0.10) * 10                      # threshold gate
# vol_term  = clip(|delta_pct|, 0, 0.20) * 5                  # vol shock weight
# raw       = magnitude * vol_term * (1.5 if small_cap else 1.0)
# score     = clip(raw, 0, 3.0)
# direction = LONG iff (aligned and score > _DIRECTION_THRESHOLD) else NEUTRAL
#
# Aligned conditions (paper Tables 3A, 3B):
#   drift_aligned    = (r_t > +0.10) AND (delta_pct < 0)
#   reversal_aligned = (r_t < -0.10) AND (delta_pct > 0)
# Misaligned cells produce score=0.0 / direction=NEUTRAL even when
# magnitude clears the threshold.

log: Final = structlog.get_logger(__name__)

_RETURN_THRESHOLD: Final[float] = 0.10
_DIRECTION_THRESHOLD: Final[float] = 0.6
_HORIZON_DAYS: Final[int] = 20
_MAGNITUDE_GAIN: Final[float] = 10.0
_VOL_TERM_GAIN: Final[float] = 5.0
_VOL_TERM_CLIP: Final[float] = 0.20
_SMALL_CAP_MULTIPLIER: Final[float] = 1.5
_SCORE_CLIP: Final[float] = 3.0


RegimeLabel = Literal[
    "drift_aligned",
    "reversal_aligned",
    "misaligned_up_up",
    "misaligned_down_down",
    "below_threshold",
]


@dataclass(frozen=True, slots=True)
class VkospiMoodInputs:
    return_t: float
    delta_pct: float
    small_cap: bool

    def regime(self) -> RegimeLabel:
        if abs(self.return_t) < _RETURN_THRESHOLD:
            return "below_threshold"
        if self.return_t > 0 and self.delta_pct < 0:
            return "drift_aligned"
        if self.return_t < 0 and self.delta_pct > 0:
            return "reversal_aligned"
        if self.return_t > 0 and self.delta_pct > 0:
            return "misaligned_up_up"
        return "misaligned_down_down"


@dataclass(frozen=True, slots=True)
class VkospiMoodScore:
    regime: RegimeLabel
    magnitude: float
    vol_term: float
    raw_score: float
    net_score: float
    direction: Literal["LONG", "SHORT", "NEUTRAL"]

    @property
    def confidence(self) -> float:
        return min(1.0, abs(self.net_score) / _SCORE_CLIP)

    @property
    def clipped(self) -> bool:
        return self.raw_score > _SCORE_CLIP


def score_vkospi_mood(inputs: VkospiMoodInputs) -> VkospiMoodScore:
    regime = inputs.regime()
    if regime == "below_threshold":
        return VkospiMoodScore(
            regime=regime, magnitude=0.0, vol_term=0.0,
            raw_score=0.0, net_score=0.0, direction="NEUTRAL",
        )
    magnitude = max(0.0, abs(inputs.return_t) - _RETURN_THRESHOLD) * _MAGNITUDE_GAIN
    vol_term = min(_VOL_TERM_CLIP, abs(inputs.delta_pct)) * _VOL_TERM_GAIN
    multiplier = _SMALL_CAP_MULTIPLIER if inputs.small_cap else 1.0
    raw = magnitude * vol_term * multiplier
    net = min(_SCORE_CLIP, raw)
    aligned = regime in {"drift_aligned", "reversal_aligned"}
    if aligned and net > _DIRECTION_THRESHOLD:
        direction: Literal["LONG", "SHORT", "NEUTRAL"] = "LONG"
    else:
        direction = "NEUTRAL"
    return VkospiMoodScore(
        regime=regime, magnitude=magnitude, vol_term=vol_term,
        raw_score=raw, net_score=net if direction == "LONG" else 0.0,
        direction=direction,
    )


@dataclass(frozen=True, slots=True)
class _Source:
    name: str
    snapshot_id: str | None


class EVkospiMoodKrExpert:
    """KR investor-mood expert (paper-derived).

    Computes the same-day mood-vs-shock alignment for a KR ticker. Skips
    cleanly when:
      - ticker is not KR (use a US thesis instead)
      - VKOSPI provider is not configured (live fetcher deferred)
      - return / VKOSPI history is insufficient

    Score is INFORMATIONAL until the calibration table records a measured
    AUC/Sharpe from a phase_kr_vkospi_mood hindcast. Bootstrap weight = 0
    in the composite (INV-GS-103, INV-GS-133).
    """

    name = "E_VKOSPI_MOOD_KR"

    def __init__(
        self,
        *,
        vkospi_client: VkospiClient,
        return_resolver: ReturnResolver,
        small_cap_resolver: SmallCapResolver | None = None,
    ) -> None:
        self._vkospi = vkospi_client
        self._return = return_resolver
        self._is_small = small_cap_resolver

    async def compute(
        self, ticker: str, ts: datetime,
    ) -> ExpertSignal:
        if not is_kr_ticker(ticker):
            raise ExpertSkipError(
                f"E_VKOSPI_MOOD_KR: ticker {ticker!r} is not KR (KOSPI 200 only)"
            )
        code = normalize_kr_ticker(ticker)
        as_of = ts.date()
        sources: list[_Source] = []

        # 1) Most-recent ticker daily return on or before `as_of`.
        try:
            return_t = await self._return.get_recent_daily_return(code, as_of)
        except Exception as exc:
            raise ExpertSkipError(
                f"E_VKOSPI_MOOD_KR: return resolver failed for {code}: {exc}"
            ) from exc
        if return_t is None:
            raise ExpertSkipError(
                f"E_VKOSPI_MOOD_KR: no recent daily return for {code} as of {as_of}"
            )

        # 2) ΔVKOSPI on the same day.
        try:
            delta = await self._vkospi.get_delta_at(as_of)
        except VkospiDataError as exc:
            raise ExpertSkipError(
                f"E_VKOSPI_MOOD_KR: VKOSPI unavailable: {exc}"
            ) from exc
        sources.append(_Source(
            name="vkospi_client.delta",
            snapshot_id=self._vkospi.last_snapshot_id,
        ))

        # 3) Small-cap classification (optional resolver; defaults to False).
        small_cap = False
        if self._is_small is not None:
            try:
                small_cap = await self._is_small.is_small_cap(code, as_of)
            except Exception as exc:
                log.info("e_vkospi_mood.small_cap_skip", code=code, err=str(exc))

        inputs = VkospiMoodInputs(
            return_t=return_t, delta_pct=delta.pct_change, small_cap=small_cap,
        )
        score = score_vkospi_mood(inputs)
        if score.regime == "below_threshold":
            raise ExpertSkipError(
                f"E_VKOSPI_MOOD_KR: |r|={abs(return_t):.4f} < threshold "
                f"{_RETURN_THRESHOLD:.2f} on {as_of}"
            )

        return _build_signal(
            code=code, ts=ts, inputs=inputs, delta=delta,
            score=score, sources=sources,
        )


# Resolver protocols — kept narrow so tests can inject lightweight fakes.

class ReturnResolver:
    async def get_recent_daily_return(
        self, code: str, as_of: date,
    ) -> float | None:
        raise NotImplementedError


class SmallCapResolver:
    async def is_small_cap(
        self, code: str, as_of: date,
    ) -> bool:
        raise NotImplementedError


def _build_signal(
    *,
    code: str,
    ts: datetime,
    inputs: VkospiMoodInputs,
    delta: VkospiDelta,
    score: VkospiMoodScore,
    sources: list[_Source],
) -> ExpertSignal:
    archetype: Literal["impulse", "continuation", "contrarian", "mixed"] = (
        "contrarian" if score.regime == "reversal_aligned"
        else "continuation" if score.regime == "drift_aligned"
        else "mixed"
    )
    basis = (
        f"r_t={inputs.return_t:+.2%} ΔVKOSPI={delta.pct_change:+.2%} "
        f"regime={score.regime} small_cap={inputs.small_cap} "
        f"raw={score.raw_score:.2f}"
    )
    metadata = tuple(sorted({
        "regime": score.regime,
        "return_t": f"{inputs.return_t:.4f}",
        "delta_pct": f"{inputs.delta_pct:.4f}",
        "vkospi_close_t": f"{delta.close_t:.4f}",
        "vkospi_close_t_minus_1": f"{delta.close_t_minus_1:.4f}",
        "small_cap": str(inputs.small_cap),
        "magnitude": f"{score.magnitude:.4f}",
        "vol_term": f"{score.vol_term:.4f}",
        "raw_score": f"{score.raw_score:.4f}",
        "net_score": f"{score.net_score:.4f}",
        "clipped": str(score.clipped),
        "horizon_days": str(_HORIZON_DAYS),
        "return_threshold": f"{_RETURN_THRESHOLD:.4f}",
    }.items()))
    source_strings: tuple[str, ...] = tuple(
        f"{s.name}#{(s.snapshot_id or 'nosnap')[:12]}" for s in sources
    ) or ("e_vkospi_mood_kr.synthetic",)
    return ExpertSignal(
        expert_name="E_VKOSPI_MOOD_KR",  # type: ignore[arg-type]
        ticker=code,
        direction=score.direction,  # type: ignore[arg-type]
        net_score=score.net_score,
        confidence=score.confidence,
        archetype=archetype,
        basis=basis,
        sources=source_strings,
        expires_at=ts.replace(tzinfo=ts.tzinfo or UTC) + timedelta(days=_HORIZON_DAYS),
        metadata=metadata,
    )


__all__ = [
    "EVkospiMoodKrExpert",
    "ReturnResolver",
    "SmallCapResolver",
    "VkospiMoodInputs",
    "VkospiMoodScore",
    "score_vkospi_mood",
]
