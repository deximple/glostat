from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Final

import structlog

from glostat.core.errors import ExpertSkipError
from glostat.core.types import ExpertSignal
from glostat.data.dart_client import (
    DartApiError,
    DartApiKeyMissingError,
    DartClient,
    is_dart_configured,
)
from glostat.data.dart_types import DartExecutiveTransaction
from glostat.data.data_router import normalize_kr_ticker

# v1.7.0 — KR Insider Velocity expert.
#
# WHY: existing E_INSIDER_KR detects "cluster" (3+ executives buying within
# 14 days). This expert is the *first derivative* of that — does the rate of
# insider buys (or sells) accelerate week-over-week? Acceleration may signal
# stronger conviction than aggregate clustering.
#
# Hypothesis: if insider BUY share volume in days [today-7, today] is much
# higher than days [today-14, today-7], that's an accelerating-conviction
# signal. Conversely for sells.
#
# Status: SKELETON for v1.7.0. Calibration is bootstrapped at n=0 (weight=0
# in composite). Hindcast wiring deferred to v1.7.1+ once a measurement run
# proves whether velocity is meaningfully different from zero-order cluster.

log: Final = structlog.get_logger(__name__)

_LOOKBACK_DAYS: Final[int] = 30
_RECENT_WINDOW: Final[int] = 7
_PRIOR_WINDOW: Final[int] = 7
_SMOOTHING: Final[float] = 1.0  # Laplace +1 to avoid log(0)
_VELOCITY_GAIN: Final[float] = 2.0
_SCORE_CLIP: Final[float] = 2.5
_DIRECTION_THRESHOLD: Final[float] = 0.6
_HORIZON_DAYS: Final[int] = 30


@dataclass(frozen=True, slots=True)
class InsiderVelocityScore:
    buys_recent: float
    buys_prior: float
    sells_recent: float
    sells_prior: float
    buy_velocity: float
    sell_velocity: float
    net_velocity: float
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


class EInsiderVelocityKrExpert:
    """KR Insider Velocity — first-derivative of E_INSIDER_KR cluster signal.

    Activates only when GLOSTAT_DART_API_KEY is configured AND the ticker is
    in the KOSPI 200 universe. Otherwise raises ExpertSkipError so the
    composite predictor cleanly degrades to other signals.

    Skeleton status (v1.7.0):
      - Score formula implemented + tested.
      - Calibration bootstrapped at n=0 (weight=0 in composite).
      - Hindcast wiring deferred to v1.7.1.
    """

    name = "E_INSIDER_VELOCITY_KR"

    def __init__(
        self,
        *,
        dart_client: DartClient | None = None,
        kospi200: frozenset[str] | None = None,
    ) -> None:
        self._dart = dart_client
        self._kospi200 = kospi200 or frozenset()

    @classmethod
    def from_env(
        cls, *, kospi200: frozenset[str] | None = None,
    ) -> EInsiderVelocityKrExpert | None:
        if not is_dart_configured():
            return None
        try:
            client = DartClient()
        except DartApiKeyMissingError:
            return None
        return cls(dart_client=client, kospi200=kospi200)

    async def compute(self, ticker: str, ts: datetime) -> ExpertSignal:
        code = normalize_kr_ticker(ticker)
        if self._kospi200 and code not in self._kospi200:
            raise ExpertSkipError(
                f"E_INSIDER_VELOCITY_KR: {code} not in KOSPI 200 universe"
            )
        if self._dart is None:
            raise ExpertSkipError(
                "E_INSIDER_VELOCITY_KR: DART API not configured "
                "(set GLOSTAT_DART_API_KEY)"
            )
        sources: list[_Source] = []
        try:
            corp_code = await self._dart.get_corp_code(code)
        except (DartApiError, DartApiKeyMissingError) as exc:
            raise ExpertSkipError(
                f"E_INSIDER_VELOCITY_KR: corp_code lookup failed for {code}: {exc}"
            ) from exc
        try:
            txns = await self._dart.get_executive_transactions(
                corp_code, lookback_days=_LOOKBACK_DAYS,
            )
        except DartApiError as exc:
            raise ExpertSkipError(
                f"E_INSIDER_VELOCITY_KR: DART transactions failed: {exc}"
            ) from exc
        snap_id = self._dart.last_snapshot_id
        if snap_id is not None:
            sources.append(_Source(
                name="dart.elestock.velocity", snapshot_id=snap_id,
            ))
        score = score_velocity(txns, today=ts.date())
        return _build_signal(code=code, ts=ts, score=score, sources=sources)


def score_velocity(
    txns: Sequence[DartExecutiveTransaction], *, today: date,
) -> InsiderVelocityScore:
    # Bucket transactions into recent_7d and prior_7d windows.
    recent_cutoff = today - timedelta(days=_RECENT_WINDOW)
    prior_cutoff = today - timedelta(days=_RECENT_WINDOW + _PRIOR_WINDOW)
    buys_recent = 0.0
    buys_prior = 0.0
    sells_recent = 0.0
    sells_prior = 0.0
    for t in txns:
        tx_date = _tx_date(t)
        if tx_date is None:
            continue
        shares = abs(_tx_shares(t))
        if shares <= 0:
            continue
        side = _tx_side(t)
        if recent_cutoff <= tx_date <= today:
            if side == "BUY":
                buys_recent += shares
            elif side == "SELL":
                sells_recent += shares
        elif prior_cutoff <= tx_date < recent_cutoff:
            if side == "BUY":
                buys_prior += shares
            elif side == "SELL":
                sells_prior += shares
    # Laplace-smoothed velocity ratios.
    buy_velocity = (buys_recent + _SMOOTHING) / (buys_prior + _SMOOTHING)
    sell_velocity = (sells_recent + _SMOOTHING) / (sells_prior + _SMOOTHING)
    # Net velocity in log space — symmetric around zero.
    net_velocity = math.log(buy_velocity) - math.log(sell_velocity)
    raw = max(
        -_SCORE_CLIP, min(_SCORE_CLIP, net_velocity * _VELOCITY_GAIN),
    )
    return InsiderVelocityScore(
        buys_recent=buys_recent, buys_prior=buys_prior,
        sells_recent=sells_recent, sells_prior=sells_prior,
        buy_velocity=buy_velocity, sell_velocity=sell_velocity,
        net_velocity=net_velocity,
        raw_score=raw, net_score=raw,
    )


def _tx_date(t: DartExecutiveTransaction) -> date | None:
    # DART bsis_dt is YYYYMMDD string. Normalize to date.
    raw = getattr(t, "bsis_dt", "") or ""
    s = str(raw).strip()
    if len(s) != 8 or not s.isdigit():
        return None
    try:
        return date(int(s[:4]), int(s[4:6]), int(s[6:8]))
    except (ValueError, TypeError):
        return None


def _tx_shares(t: DartExecutiveTransaction) -> float:
    # DART sp_stock_lmp_irds_cnt is signed change in shares (string with commas).
    raw = getattr(t, "sp_stock_lmp_irds_cnt", "") or ""
    s = str(raw).strip().replace(",", "")
    if not s or s in {"-", "nan"}:
        return 0.0
    try:
        return float(s)
    except (TypeError, ValueError):
        return 0.0


def _tx_side(t: DartExecutiveTransaction) -> str:
    # DartExecutiveTransaction has explicit is_buy/is_sell flags.
    if getattr(t, "is_buy", False):
        return "BUY"
    if getattr(t, "is_sell", False):
        return "SELL"
    return "OTHER"


def _build_signal(
    *,
    code: str,
    ts: datetime,
    score: InsiderVelocityScore,
    sources: list[_Source],
) -> ExpertSignal:
    basis = (
        f"buy_velocity={score.buy_velocity:.2f} "
        f"({score.buys_recent:.0f}/{score.buys_prior:.0f}+1), "
        f"sell_velocity={score.sell_velocity:.2f} "
        f"({score.sells_recent:.0f}/{score.sells_prior:.0f}+1), "
        f"net_log={score.net_velocity:+.3f}, raw={score.raw_score:+.2f}"
    )
    metadata = tuple(sorted({
        "buys_recent": f"{score.buys_recent:.4f}",
        "buys_prior": f"{score.buys_prior:.4f}",
        "sells_recent": f"{score.sells_recent:.4f}",
        "sells_prior": f"{score.sells_prior:.4f}",
        "buy_velocity": f"{score.buy_velocity:.4f}",
        "sell_velocity": f"{score.sell_velocity:.4f}",
        "net_velocity": f"{score.net_velocity:.4f}",
        "raw_score": f"{score.raw_score:.4f}",
        "net_score": f"{score.net_score:.4f}",
        "code": code,
    }.items()))
    source_strings: tuple[str, ...] = tuple(
        f"{s.name}#{s.snapshot_id[:12]}" for s in sources
    ) or ("e_insider_velocity_kr.synthetic",)
    return ExpertSignal(
        expert_name="E_INSIDER_VELOCITY_KR",  # type: ignore[arg-type]
        ticker=code,
        direction=score.direction,  # type: ignore[arg-type]
        net_score=score.net_score,
        confidence=score.confidence,
        archetype="continuation",
        basis=basis,
        sources=source_strings,
        expires_at=ts + timedelta(days=_HORIZON_DAYS),
        metadata=metadata,
    )


__all__ = [
    "EInsiderVelocityKrExpert",
    "InsiderVelocityScore",
    "score_velocity",
]
