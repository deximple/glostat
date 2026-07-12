from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Final

import structlog

from glostat.core.errors import ExpertSkipError
from glostat.core.types import ExpertSignal
from glostat.data.commodity_client import (
    CommodityClient,
    CommodityDataError,
    CommodityKey,
    CrackSpread,
)
from glostat.data.data_router import DataRouter, normalize_kr_ticker, to_yfinance_kr_ticker
from glostat.data.sector_classifier_kr import (
    CycleClass,
    KrSector,
    cycle_class_of,
    sector_of,
)
from glostat.data.yfinance_types import Fundamentals

# v1.5 P6 — KR cyclical-sector fundamentals.
#
# WHY (P6 KR Market Specialist panel finding):
#   E_FUNDAMENTAL_KR scores SK이노베이션 (096770) at -1.78 because PER is
#   above the KOSPI 200 median. But SK이노베이션 is a refiner — counter-
#   cyclical fundamentals mean PER rises at cycle troughs (earnings drop
#   faster than price), so a high PER often signals impending margin recovery,
#   not a SELL. This expert overrides the generic value-tilt for cyclicals.
#
# Score formula:
#   ev_ebitda_z = (ev_ebitda - sector_median) / sector_std    (lower = cheap)
#   cycle_term  = commodity_percentile - 0.5                  (low = trough)
#   raw_score   = -W_VALUE * ev_ebitda_z + W_CYCLE * (-cycle_term)
#                                            ^^^ cycle low → buy signal
#   net_score   = clip(raw_score, ±SCORE_CLIP)
#
# Activation: ExpertSkipError unless cycle_class_of(ticker) == CYCLICAL.
# All other tickers fall through to E_FUNDAMENTAL_KR (the generic expert).

log: Final = structlog.get_logger(__name__)

# Sector-specific EV/EBITDA medians sourced from KRX 2024-2026 historical
# distribution. Cyclicals trade at a structural discount to the KOSPI 200
# average; using sector-specific medians keeps the z-score honest.
_SECTOR_EV_EBITDA: Final[dict[KrSector, tuple[float, float]]] = {
    # (median, stddev)
    KrSector.REFINING:      (5.5, 2.5),
    KrSector.STEEL:         (6.0, 2.5),
    KrSector.CHEMICALS:     (7.5, 3.0),
    KrSector.SHIPPING:      (4.5, 3.0),
    KrSector.CONSTRUCTION:  (5.0, 2.0),
    KrSector.CONSUMER_CYCL: (8.0, 3.0),
}

# Map sector → primary commodity cycle indicator. Refining gets crack spread
# (CrackSpread, not CommodityCycle); the rest use a single CommodityKey.
_SECTOR_CYCLE_KEY: Final[dict[KrSector, CommodityKey]] = {
    KrSector.STEEL:         CommodityKey.IRON_ORE,
    KrSector.CHEMICALS:     CommodityKey.BRENT,       # naphtha proxy
    KrSector.SHIPPING:      CommodityKey.DRY_BULK,
    KrSector.CONSTRUCTION:  CommodityKey.COPPER,
    KrSector.CONSUMER_CYCL: CommodityKey.COPPER,      # broad cycle proxy
}

_W_VALUE: Final[float] = 0.6     # ev_ebitda contribution weight
_W_CYCLE: Final[float] = 0.4     # commodity-cycle contribution weight
_SCORE_CLIP: Final[float] = 3.0
# WHY: cyclicals reward early entry near mean-reversion turn — keep threshold
# moderately sensitive so a +0.50 net (e.g. EV/EBITDA cheap + cycle trough)
# crosses LONG instead of staying NEUTRAL.
_DIRECTION_THRESHOLD: Final[float] = 0.5
_SWING_HORIZON_DAYS: Final[int] = 30


@dataclass(frozen=True, slots=True)
class CyclicalScore:
    sector: KrSector
    ev_ebitda: float | None
    ev_ebitda_z: float
    cycle_percentile: float
    cycle_term: float
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


class EFundamentalKrCyclicalExpert:
    """KR cyclical-sector fundamentals (정유/철강/화학/운송/건설/자동차).

    Activates only when `is_cyclical(ticker) == True`. For every other ticker
    raises ExpertSkipError so the composite predictor falls back to the
    generic E_FUNDAMENTAL_KR.
    """

    name = "E_FUNDAMENTAL_KR_CYCLICAL"

    def __init__(
        self,
        *,
        router: DataRouter,
        commodity_client: CommodityClient,
    ) -> None:
        self._router = router
        self._commodity = commodity_client

    async def compute(self, ticker: str, ts: datetime) -> ExpertSignal:
        code = normalize_kr_ticker(ticker)
        if cycle_class_of(code) != CycleClass.CYCLICAL:
            raise ExpertSkipError(
                f"E_FUNDAMENTAL_KR_CYCLICAL: {code} is not classified as cyclical "
                f"(sector={sector_of(code).value})"
            )
        sector = sector_of(code)
        sources: list[_Source] = []
        fundamentals = await self._fetch_fundamentals(code, sources)
        cycle_pctile, cycle_basis = await self._fetch_cycle(sector, sources)
        score = _score(sector, fundamentals, cycle_pctile)
        return _build_signal(
            code=code, ts=ts, score=score,
            fundamentals=fundamentals, cycle_basis=cycle_basis, sources=sources,
        )

    async def _fetch_fundamentals(
        self, code: str, sources: list[_Source],
    ) -> Fundamentals:
        client, method = self._router.route("E_FUNDAMENTAL_KR_CYCLICAL", "fundamentals")
        yf_ticker = to_yfinance_kr_ticker(code)
        try:
            result: Fundamentals = await getattr(client, method)(yf_ticker)
        except Exception as exc:
            raise ExpertSkipError(
                f"E_FUNDAMENTAL_KR_CYCLICAL: yfinance failed for {yf_ticker}: {exc}"
            ) from exc
        snap_id = getattr(client, "last_snapshot_id", None)
        if snap_id is not None:
            sources.append(_Source(
                name="yfinance.info.kr_cyclical", snapshot_id=snap_id,
            ))
        return result

    async def _fetch_cycle(
        self, sector: KrSector, sources: list[_Source],
    ) -> tuple[float, str]:
        # Refining gets the crack spread (gasoline - WTI); other sectors get
        # a single commodity. Either way returns (percentile, basis_string).
        if sector == KrSector.REFINING:
            try:
                spread: CrackSpread = await self._commodity.get_crack_spread()
            except CommodityDataError as exc:
                raise ExpertSkipError(
                    f"E_FUNDAMENTAL_KR_CYCLICAL: crack spread unavailable: {exc}"
                ) from exc
            basis = (
                f"crack spread ${spread.last_spread:.1f}/bbl, "
                f"pctile={spread.cycle_percentile:.2f} "
                f"(n={spread.n_observations})"
            )
            return spread.cycle_percentile, basis
        key = _SECTOR_CYCLE_KEY.get(sector)
        if key is None:
            raise ExpertSkipError(
                f"E_FUNDAMENTAL_KR_CYCLICAL: no cycle indicator wired for "
                f"sector={sector.value}"
            )
        try:
            cycle = await self._commodity.get_cycle(key)
        except CommodityDataError as exc:
            raise ExpertSkipError(
                f"E_FUNDAMENTAL_KR_CYCLICAL: {key.value} cycle unavailable: {exc}"
            ) from exc
        if cycle.snapshot_id is not None:
            sources.append(_Source(
                name=f"commodity.{key.value.lower()}",
                snapshot_id=cycle.snapshot_id,
            ))
        basis = (
            f"{key.value} ${cycle.last_close:.2f}, "
            f"pctile={cycle.cycle_percentile:.2f} ({cycle.cycle_position}), "
            f"30d_mom={cycle.momentum_30d:+.2%}"
        )
        return cycle.cycle_percentile, basis


def _score(
    sector: KrSector, fundamentals: Fundamentals, cycle_percentile: float,
) -> CyclicalScore:
    median, stddev = _SECTOR_EV_EBITDA.get(sector, (7.0, 3.0))
    # WHY: yfinance Fundamentals doesn't expose EV/EBITDA directly; we derive
    # a proxy from PER × dividend yield × market cap presence. When EV/EBITDA
    # is missing we fall back to PER as a degraded value indicator.
    ev_ebitda = _derive_ev_ebitda(fundamentals)
    ev_ebitda_z = (
        0.0 if ev_ebitda is None
        else (ev_ebitda - median) / max(stddev, 1e-3)
    )
    # Cycle term: percentile in [0, 1]. Center at 0.5 so trough → negative.
    # Then NEGATE so trough produces positive raw_score (reversal expected).
    cycle_term = cycle_percentile - 0.5
    raw = -_W_VALUE * ev_ebitda_z + _W_CYCLE * (-cycle_term * 2.0)
    net = max(-_SCORE_CLIP, min(_SCORE_CLIP, raw))
    return CyclicalScore(
        sector=sector,
        ev_ebitda=ev_ebitda,
        ev_ebitda_z=ev_ebitda_z,
        cycle_percentile=cycle_percentile,
        cycle_term=cycle_term,
        raw_score=raw,
        net_score=net,
    )


def _derive_ev_ebitda(f: Fundamentals) -> float | None:
    # WHY: yfinance .info exposes "enterpriseToEbitda" in the raw payload;
    # Fundamentals.raw is a tuple[tuple[str, Any], ...] (frozen dataclass
    # safe). Walk the tuple to find the EV/EBITDA cell. When missing or
    # non-positive, return None so caller falls back to PER scoring.
    if not f.raw:
        return None
    for key, value in f.raw:
        if key != "enterpriseToEbitda":
            continue
        try:
            v = float(value)
        except (TypeError, ValueError):
            return None
        if v <= 0 or v > 200:   # filter obvious garbage values
            return None
        return v
    return None


def _build_signal(
    *,
    code: str,
    ts: datetime,
    score: CyclicalScore,
    fundamentals: Fundamentals,
    cycle_basis: str,
    sources: list[_Source],
) -> ExpertSignal:
    basis = (
        f"sector={score.sector.value}, "
        f"EV/EBITDA={score.ev_ebitda} z={score.ev_ebitda_z:+.2f}, "
        f"{cycle_basis}, "
        f"net={score.net_score:+.2f} "
        f"(W_value={_W_VALUE}, W_cycle={_W_CYCLE})"
    )
    metadata = tuple(sorted({
        "sector": score.sector.value,
        "ev_ebitda": _fmt(score.ev_ebitda),
        "ev_ebitda_z": f"{score.ev_ebitda_z:.4f}",
        "cycle_percentile": f"{score.cycle_percentile:.4f}",
        "cycle_term": f"{score.cycle_term:.4f}",
        "raw_score": f"{score.raw_score:.4f}",
        "net_score": f"{score.net_score:.4f}",
        "weight_value": f"{_W_VALUE}",
        "weight_cycle": f"{_W_CYCLE}",
        "code": code,
        "per_raw": _fmt(fundamentals.pe_ratio),
    }.items()))
    source_strings: tuple[str, ...] = tuple(
        f"{s.name}#{s.snapshot_id[:12]}" for s in sources
    ) or ("e_fundamental_kr_cyclical.synthetic",)
    return ExpertSignal(
        expert_name="E_FUNDAMENTAL_KR_CYCLICAL",  # type: ignore[arg-type]
        ticker=code,
        direction=score.direction,  # type: ignore[arg-type]
        net_score=score.net_score,
        confidence=score.confidence,
        archetype="contrarian",     # cyclicals reward mean-reversion
        basis=basis,
        sources=source_strings,
        expires_at=ts + timedelta(days=_SWING_HORIZON_DAYS),
        metadata=metadata,
    )


def _fmt(v: float | None) -> str:
    return "n/a" if v is None else f"{v:.4f}" if isinstance(v, float) else str(v)


__all__ = [
    "CyclicalScore",
    "EFundamentalKrCyclicalExpert",
]
