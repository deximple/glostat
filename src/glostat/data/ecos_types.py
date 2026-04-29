from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime

# v1.3 M2 — ECOS (한국은행 경제통계시스템) value types. Pure dataclasses.
# Mirrors dart_types.py shape for symmetry across KR data clients.


@dataclass(frozen=True, slots=True)
class EcosObservation:
    stat_code: str          # e.g. "722Y001" — 한국은행 기준금리
    item_code: str          # e.g. "0101000"
    period: str             # "YYYYMM" (M cycle) or "YYYYMMDD" (D cycle)
    value: float | None     # numeric DATA_VALUE; None if "-" / blank
    unit: str = ""          # "연%" / "원" / etc.
    ts_fetched: datetime = field(default_factory=lambda: datetime.now(tz=UTC))

    @property
    def period_date(self) -> date | None:
        s = (self.period or "").strip()
        try:
            if len(s) == 6 and s.isdigit():
                return date(int(s[:4]), int(s[4:6]), 1)
            if len(s) == 8 and s.isdigit():
                return date(int(s[:4]), int(s[4:6]), int(s[6:8]))
        except ValueError:
            return None
        return None


@dataclass(frozen=True, slots=True)
class EcosSeries:
    stat_code: str
    item_code: str
    cycle: str              # "D" / "M" / "Q" / "A"
    observations: tuple[EcosObservation, ...] = field(default_factory=tuple)

    def latest(self) -> EcosObservation | None:
        if not self.observations:
            return None
        return self.observations[-1]

    def values(self) -> tuple[float, ...]:
        return tuple(o.value for o in self.observations if o.value is not None)

    def n_valid(self) -> int:
        return sum(1 for o in self.observations if o.value is not None)


class EcosApiKeyMissingError(NotImplementedError):
    """Raised when GLOSTAT_ECOS_API_KEY is not configured."""

    @classmethod
    def make(cls) -> EcosApiKeyMissingError:
        return cls(
            "ECOS API key missing. Register at "
            "https://ecos.bok.or.kr/jsp/openapi/OpenApiController.jsp?t=mainPage "
            "(free, 10,000 calls/day) and export GLOSTAT_ECOS_API_KEY=<key>. "
            "Without this, KR macro signal (E_MACRO_KR) is skipped."
        )


def _parse_value(raw: str | None) -> float | None:
    if raw is None:
        return None
    s = raw.strip()
    if not s or s in {"-", "nan", "NaN"}:
        return None
    try:
        return float(s.replace(",", ""))
    except ValueError:
        return None


__all__ = [
    "EcosApiKeyMissingError",
    "EcosObservation",
    "EcosSeries",
    "_parse_value",
]
