from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Final

import structlog

from glostat.core.errors import ExpertSkipError
from glostat.core.types import ExpertSignal
from glostat.data.data_router import is_kr_ticker, normalize_kr_ticker
from glostat.data.ecos_client import (
    EcosApiError,
    EcosApiKeyMissingError,
    EcosClient,
    is_ecos_configured,
)
from glostat.data.ecos_types import EcosSeries

# v1.3 M2 — KR macro expert.
#
# WHY: KR equity drift is materially driven by macro context (BoK base rate
# direction, KRW/USD trend for exporter conviction, CPI surprises ahead of
# tightening, KOSPI index momentum). ECOS (한국은행 OpenAPI) is the canonical
# free source for these series. Symmetric in spirit to a US E_MACRO (FRED-backed,
# deferred Phase 2) — for KR the data is already free + structured today.
#
# Universe: ALL KR tickers (XKRX + XKOS). Macro applies broadly, so universe
# enforcement happens at the wrapper layer (skip on non-KR ticker) rather than
# in-expert. ECOS missing key → ExpertSkipError so the composite predictor sees
# a clean skip with `docs/ECOS_API_SETUP.md` pointer.

log: Final = structlog.get_logger(__name__)

# Aggregation weights (see report; reviewable). Net score in [-3, +3].
_WEIGHT_BASE_RATE: Final[float] = 1.0      # cuts → equity bullish (sign-flipped)
_WEIGHT_FX: Final[float] = 0.5             # KRW weakening → exporter bullish
_WEIGHT_CPI: Final[float] = 0.7            # above-trend CPI → tightening fear, bearish
_WEIGHT_KOSPI: Final[float] = 0.8          # index momentum continuation
_SCORE_CLIP: Final[float] = 3.0
_DIRECTION_THRESHOLD: Final[float] = 0.6   # KR macro is steady; relax bar

_BASE_RATE_LOOKBACK_M: Final[int] = 4      # 3-month change needs 4 months of data
_FX_LOOKBACK_D: Final[int] = 90            # 60-day window + buffer for weekends
_CPI_LOOKBACK_M: Final[int] = 14           # 12-month trailing avg + 2 months margin
_KOSPI_LOOKBACK_D: Final[int] = 90         # 60-day momentum + buffer

_HORIZON_DAYS: Final[int] = 30
# Default per-stock export exposure (megacap exporters dominate the KOSPI 200).
# Universe-aware tilt deferred to follow-up (sector-stat resolver).
_EXPORT_EXPOSURE_DEFAULT: Final[float] = 1.0


@dataclass(frozen=True, slots=True)
class MacroKrInputs:
    base_rate_change_3m: float | None       # latest minus 3 months ago, in %-points
    krw_usd_trend_60d: float | None         # (latest / 60d-ago) - 1, signed
    cpi_surprise: float | None              # latest CPI minus trailing 12m mean
    kospi_momentum_60d: float | None        # (latest / 60d-ago) - 1, signed


@dataclass(frozen=True, slots=True)
class MacroKrScore:
    base_rate_term: float
    fx_term: float
    cpi_term: float
    kospi_term: float
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
    snapshot_id: str
    ts: datetime


class EMacroKrExpert:
    """KR macro expert — ECOS-backed (BoK base rate, KRW/USD, CPI, KOSPI).

    Skip cleanly when ECOS key is unavailable so the composite predictor surfaces
    `set GLOSTAT_ECOS_API_KEY` rather than crashing. Universe gating (KR-only)
    happens at the wrapper level.
    """

    name = "E_MACRO_KR"

    def __init__(
        self,
        *,
        ecos_client: EcosClient | None = None,
        export_exposure: float = _EXPORT_EXPOSURE_DEFAULT,
    ) -> None:
        self._ecos = ecos_client
        self._export_exposure = export_exposure

    @classmethod
    def from_env(cls) -> EMacroKrExpert | None:
        # Returns None when ECOS isn't configured (mirrors EInsiderKrExpert.from_env).
        if not is_ecos_configured():
            return None
        try:
            client = EcosClient()
        except EcosApiKeyMissingError:
            return None
        return cls(ecos_client=client)

    async def compute(self, ticker: str, ts: datetime) -> ExpertSignal:
        if self._ecos is None:
            raise ExpertSkipError(
                "E_MACRO_KR: ECOS client not configured "
                "(see docs/ECOS_API_SETUP.md)"
            )
        if not is_kr_ticker(ticker):
            raise ExpertSkipError(
                f"E_MACRO_KR: ticker {ticker!r} not KR equity"
            )
        code = normalize_kr_ticker(ticker)
        sources: list[_Source] = []
        as_of = ts.date()
        inputs = await self._fetch_inputs(as_of, sources)
        if _all_inputs_missing(inputs):
            raise ExpertSkipError(
                f"E_MACRO_KR: no usable ECOS series for {as_of.isoformat()}"
            )
        score = score_macro_kr(inputs, export_exposure=self._export_exposure)
        return _build_signal(
            code=code, ts=ts, inputs=inputs, score=score, sources=sources,
        )

    async def _fetch_inputs(
        self, as_of: date, sources: list[_Source],
    ) -> MacroKrInputs:
        # Each fetch is best-effort; failures degrade to None so a partial
        # macro picture still produces a signal.
        base = await self._safe_series(
            "ecos.base_rate", sources,
            self._ecos.get_base_rate, as_of - timedelta(days=_BASE_RATE_LOOKBACK_M * 35), as_of,
        )
        fx = await self._safe_series(
            "ecos.krw_usd", sources,
            self._ecos.get_krw_usd, as_of - timedelta(days=_FX_LOOKBACK_D), as_of,
        )
        cpi = await self._safe_series(
            "ecos.cpi", sources,
            self._ecos.get_cpi, as_of - timedelta(days=_CPI_LOOKBACK_M * 35), as_of,
        )
        kospi = await self._safe_series(
            "ecos.kospi", sources,
            self._ecos.get_kospi_index, as_of - timedelta(days=_KOSPI_LOOKBACK_D), as_of,
        )
        return MacroKrInputs(
            base_rate_change_3m=_rolling_change(base, periods=3),
            krw_usd_trend_60d=_relative_change(fx, lookback=60),
            cpi_surprise=_cpi_surprise(cpi, trailing_n=12),
            kospi_momentum_60d=_relative_change(kospi, lookback=60),
        )

    async def _safe_series(
        self,
        snap_name: str,
        sources: list[_Source],
        fetch_fn,
        start: date,
        end: date,
    ) -> EcosSeries | None:
        try:
            series = await fetch_fn(start, end)
        except (EcosApiError, EcosApiKeyMissingError) as exc:
            log.info("e_macro_kr.series_skip", source=snap_name, err=str(exc))
            return None
        snap_id = self._ecos.last_snapshot_id if self._ecos else None
        if snap_id is not None:
            sources.append(_Source(name=snap_name, snapshot_id=snap_id,
                                   ts=datetime.now(tz=UTC)))
        return series


# ── pure scoring helpers (testable without network) ─────────────────────────


def _all_inputs_missing(inputs: MacroKrInputs) -> bool:
    return all(
        v is None for v in (
            inputs.base_rate_change_3m, inputs.krw_usd_trend_60d,
            inputs.cpi_surprise, inputs.kospi_momentum_60d,
        )
    )


def _rolling_change(series: EcosSeries | None, *, periods: int) -> float | None:
    if series is None:
        return None
    vals = series.values()
    if len(vals) <= periods:
        return None
    # latest minus value `periods` rows back. WHY: BoK base rate is monthly so
    # a 3-period change ≈ 3-month change in basis-points-of-a-percent.
    return vals[-1] - vals[-1 - periods]


def _relative_change(series: EcosSeries | None, *, lookback: int) -> float | None:
    if series is None:
        return None
    vals = series.values()
    if not vals:
        return None
    # Daily series: lookback by row count; cap at series length.
    idx = max(0, len(vals) - 1 - lookback)
    base = vals[idx]
    latest = vals[-1]
    if base == 0:
        return None
    return (latest / base) - 1.0


def _cpi_surprise(series: EcosSeries | None, *, trailing_n: int) -> float | None:
    if series is None:
        return None
    vals = series.values()
    if len(vals) <= 1:
        return None
    latest = vals[-1]
    sample = vals[-(trailing_n + 1):-1] or vals[:-1]
    mean = sum(sample) / len(sample)
    if mean == 0:
        return None
    # WHY: pp deviation from trailing mean (positive = above trend → tightening risk).
    return (latest - mean) / mean


def score_macro_kr(
    inputs: MacroKrInputs,
    *,
    export_exposure: float = _EXPORT_EXPOSURE_DEFAULT,
) -> MacroKrScore:
    # Each term is bounded so a single fat tail can't dominate.
    base_term = _signed_term(
        inputs.base_rate_change_3m, scale_pp=0.50, weight=_WEIGHT_BASE_RATE,
        invert=True,
    )
    fx_term = _signed_term(
        inputs.krw_usd_trend_60d, scale_pp=0.05, weight=_WEIGHT_FX * export_exposure,
        invert=False,
    )
    cpi_term = _signed_term(
        inputs.cpi_surprise, scale_pp=0.02, weight=_WEIGHT_CPI, invert=True,
    )
    kospi_term = _signed_term(
        inputs.kospi_momentum_60d, scale_pp=0.05, weight=_WEIGHT_KOSPI, invert=False,
    )
    raw = base_term + fx_term + cpi_term + kospi_term
    net = max(-_SCORE_CLIP, min(_SCORE_CLIP, raw))
    return MacroKrScore(
        base_rate_term=base_term, fx_term=fx_term, cpi_term=cpi_term,
        kospi_term=kospi_term, raw_score=raw, net_score=net,
    )


def _signed_term(
    value: float | None, *, scale_pp: float, weight: float, invert: bool,
) -> float:
    if value is None:
        return 0.0
    # Normalize to ±2 stddev band, then weight and (optionally) flip sign.
    z = max(-2.0, min(2.0, value / max(scale_pp, 1e-9)))
    return -weight * z if invert else weight * z


# ── signal builder ──────────────────────────────────────────────────────────


def _build_signal(
    *,
    code: str,
    ts: datetime,
    inputs: MacroKrInputs,
    score: MacroKrScore,
    sources: list[_Source],
) -> ExpertSignal:
    basis = (
        f"BoK Δ3m={_fmt(inputs.base_rate_change_3m)}pp · "
        f"KRW/USD 60d={_fmt_pct(inputs.krw_usd_trend_60d)} · "
        f"CPI surprise={_fmt_pct(inputs.cpi_surprise)} · "
        f"KOSPI 60d={_fmt_pct(inputs.kospi_momentum_60d)}"
    )
    metadata: tuple[tuple[str, str], ...] = tuple(
        sorted({
            "base_rate_change_3m": _fmt(inputs.base_rate_change_3m),
            "krw_usd_trend_60d": _fmt(inputs.krw_usd_trend_60d),
            "cpi_surprise": _fmt(inputs.cpi_surprise),
            "kospi_momentum_60d": _fmt(inputs.kospi_momentum_60d),
            "base_term": f"{score.base_rate_term:.4f}",
            "fx_term": f"{score.fx_term:.4f}",
            "cpi_term": f"{score.cpi_term:.4f}",
            "kospi_term": f"{score.kospi_term:.4f}",
            "raw_score": f"{score.raw_score:.4f}",
            "net_score": f"{score.net_score:.4f}",
            "clipped": str(score.clipped),
            "code": code,
        }.items())
    )
    source_strings: tuple[str, ...] = tuple(
        f"{s.name}#{s.snapshot_id[:12]}" for s in sources
    ) or ("e_macro_kr.synthetic",)
    return ExpertSignal(
        expert_name="E_MACRO_KR",  # type: ignore[arg-type]
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


def _fmt(v: float | None) -> str:
    return "n/a" if v is None else f"{v:.4f}"


def _fmt_pct(v: float | None) -> str:
    return "n/a" if v is None else f"{v * 100:.2f}%"


__all__ = [
    "EMacroKrExpert",
    "MacroKrInputs",
    "MacroKrScore",
    "score_macro_kr",
]
