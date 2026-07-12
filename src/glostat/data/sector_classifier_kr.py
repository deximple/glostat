from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Final

from glostat.data.data_router import normalize_kr_ticker

# v1.5 P6 — KOSPI 200 ticker → sector + cyclical/defensive/growth classifier.
#
# WHY: P6 KR Market Specialist panel showed E_FUNDAMENTAL_KR's PER scoring is
# wrong for cyclicals (정유주는 사이클 저점에서 PER 상승 = healthy, not bearish).
# A sector classifier lets E_FUNDAMENTAL_KR_CYCLICAL gate itself to cyclical
# tickers and override the generic value-tilt with sector-cycle-aware logic.
#
# Mapping policy: hard-coded for the cyclical subset (where wrong scoring is
# most damaging) and "other" for the rest. KSIC programmatic lookup deferred
# to v1.6+ once the calendar/PEAD work needs sector grouping.

class KrSector(StrEnum):
    REFINING        = "refining"           # 정유 — crack spread driven
    STEEL           = "steel"              # 철강 — iron ore driven
    CHEMICALS       = "chemicals"          # 화학 — naphtha/ethylene
    SHIPPING        = "shipping"           # 운송/해운 — BDI driven
    CONSTRUCTION    = "construction"       # 건설 — copper / housing cycle
    SEMICONDUCTOR   = "semiconductor"      # 반도체 — DRAM cycle (growth, not cyclical here)
    INTERNET        = "internet"           # 인터넷/플랫폼 — growth
    BIO             = "bio"                # 바이오/제약 — growth
    BANK            = "bank"               # 은행 — defensive (rate-sensitive)
    TELECOM         = "telecom"            # 통신 — defensive
    UTILITY         = "utility"            # 유틸리티 — defensive
    CONSUMER_STAPLE = "consumer_staple"    # 필수소비재 — defensive
    CONSUMER_CYCL   = "consumer_cyclical"  # 자동차/유통 — mid (cyclical-leaning)
    OTHER           = "other"


class CycleClass(StrEnum):
    CYCLICAL    = "cyclical"
    DEFENSIVE   = "defensive"
    GROWTH      = "growth"
    OTHER       = "other"


_CYCLICAL_SECTORS: Final[frozenset[KrSector]] = frozenset({
    KrSector.REFINING,
    KrSector.STEEL,
    KrSector.CHEMICALS,
    KrSector.SHIPPING,
    KrSector.CONSTRUCTION,
    KrSector.CONSUMER_CYCL,
})

_DEFENSIVE_SECTORS: Final[frozenset[KrSector]] = frozenset({
    KrSector.BANK,
    KrSector.TELECOM,
    KrSector.UTILITY,
    KrSector.CONSUMER_STAPLE,
})

_GROWTH_SECTORS: Final[frozenset[KrSector]] = frozenset({
    KrSector.SEMICONDUCTOR,
    KrSector.INTERNET,
    KrSector.BIO,
})


# Hard-coded KOSPI 200 cyclical roster (most damaging mis-scoring lives here).
# Source: KRX 2026-04 sector groupings; verified against ticker → company name
# table. Keep this list small + deliberate; expand only when a concrete
# mis-scoring case is reported.
_TICKER_SECTOR: Final[dict[str, KrSector]] = {
    # ── Refining (정유) ────────────────────────────────────────────────────
    "010950": KrSector.REFINING,         # S-Oil
    "096770": KrSector.REFINING,         # SK이노베이션
    "078930": KrSector.REFINING,         # GS (holdings; refining-heavy)
    "267250": KrSector.REFINING,         # HD현대 (HD현대오일뱅크 모회사)
    "011170": KrSector.CHEMICALS,        # 롯데케미칼 (clinically chemicals; included
                                          # as chemical-cyclical, not refining)
    # ── Steel (철강) ───────────────────────────────────────────────────────
    "005490": KrSector.STEEL,            # POSCO홀딩스
    "004020": KrSector.STEEL,            # 현대제철
    "001230": KrSector.STEEL,            # 동국제강
    "058430": KrSector.STEEL,            # 포스코퓨처엠 (steel-adjacent battery)
    # ── Chemicals (화학) ──────────────────────────────────────────────────
    "051910": KrSector.CHEMICALS,        # LG화학
    "298020": KrSector.CHEMICALS,        # 효성티앤씨
    "009830": KrSector.CHEMICALS,        # 한화솔루션
    "005420": KrSector.CHEMICALS,        # 코스모화학
    "069620": KrSector.CHEMICALS,        # 대웅
    # ── Shipping (운송/해운) ─────────────────────────────────────────────
    "011200": KrSector.SHIPPING,         # HMM
    "180640": KrSector.SHIPPING,         # 한진칼 (대한항공 지주)
    "003490": KrSector.SHIPPING,         # 대한항공
    "020560": KrSector.SHIPPING,         # 아시아나항공
    # ── Construction (건설) ──────────────────────────────────────────────
    "000720": KrSector.CONSTRUCTION,     # 현대건설
    "047040": KrSector.CONSTRUCTION,     # 대우건설
    "375500": KrSector.CONSTRUCTION,     # DL이앤씨
    "028050": KrSector.CONSTRUCTION,     # 삼성E&A (구 삼성엔지니어링)
    "006360": KrSector.CONSTRUCTION,     # GS건설
    # ── Consumer cyclical (자동차) ──────────────────────────────────────
    "005380": KrSector.CONSUMER_CYCL,    # 현대차
    "000270": KrSector.CONSUMER_CYCL,    # 기아
    "012330": KrSector.CONSUMER_CYCL,    # 현대모비스
    # ── Growth / defensive (for ticker-aware skip messaging) ─────────────
    "005930": KrSector.SEMICONDUCTOR,    # 삼성전자
    "000660": KrSector.SEMICONDUCTOR,    # SK하이닉스
    "035420": KrSector.INTERNET,         # NAVER
    "035720": KrSector.INTERNET,         # 카카오
    "207940": KrSector.BIO,              # 삼성바이오로직스
    "068270": KrSector.BIO,              # 셀트리온
    "017670": KrSector.TELECOM,          # SK텔레콤
    "030200": KrSector.TELECOM,          # KT
    "032640": KrSector.TELECOM,          # LG유플러스
    "015760": KrSector.UTILITY,          # 한국전력
    "036460": KrSector.UTILITY,          # 한국가스공사
    "105560": KrSector.BANK,             # KB금융
    "055550": KrSector.BANK,             # 신한지주
    "086790": KrSector.BANK,             # 하나금융지주
    "316140": KrSector.BANK,             # 우리금융지주
    "033780": KrSector.CONSUMER_STAPLE,  # KT&G
}


@dataclass(frozen=True, slots=True)
class SectorInfo:
    ticker: str
    sector: KrSector
    cycle_class: CycleClass

    @property
    def is_cyclical(self) -> bool:
        return self.cycle_class == CycleClass.CYCLICAL


def sector_of(ticker: str) -> KrSector:
    code = normalize_kr_ticker(ticker)
    return _TICKER_SECTOR.get(code, KrSector.OTHER)


def cycle_class_of(ticker: str) -> CycleClass:
    sector = sector_of(ticker)
    if sector in _CYCLICAL_SECTORS:
        return CycleClass.CYCLICAL
    if sector in _DEFENSIVE_SECTORS:
        return CycleClass.DEFENSIVE
    if sector in _GROWTH_SECTORS:
        return CycleClass.GROWTH
    return CycleClass.OTHER


def is_cyclical(ticker: str) -> bool:
    return cycle_class_of(ticker) == CycleClass.CYCLICAL


def is_refining(ticker: str) -> bool:
    return sector_of(ticker) == KrSector.REFINING


def info_for(ticker: str) -> SectorInfo:
    code = normalize_kr_ticker(ticker)
    sector = _TICKER_SECTOR.get(code, KrSector.OTHER)
    return SectorInfo(ticker=code, sector=sector, cycle_class=cycle_class_of(code))


def cyclical_universe() -> tuple[str, ...]:
    return tuple(sorted(t for t, s in _TICKER_SECTOR.items() if s in _CYCLICAL_SECTORS))


def refining_universe() -> tuple[str, ...]:
    return tuple(sorted(t for t, s in _TICKER_SECTOR.items() if s == KrSector.REFINING))


__all__ = [
    "CycleClass",
    "KrSector",
    "SectorInfo",
    "cycle_class_of",
    "cyclical_universe",
    "info_for",
    "is_cyclical",
    "is_refining",
    "refining_universe",
    "sector_of",
]
