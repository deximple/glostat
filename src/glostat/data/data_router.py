from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final, Literal

import structlog

from glostat.core.errors import ConfigError

# Phase-gated routing of (expert, data_type) → concrete client + method.
# Source: PLAN_v0.6 §1.2. Enforces INV-GS-039 (phase) and INV-GS-040 (consent).

log: Final = structlog.get_logger(__name__)

Phase = Literal["mvp", "phase_2", "phase_3"]

_BUDGET_YAML_DEFAULT: Final = Path(__file__).resolve().parents[3] / "configs" / "budget.yaml"


@dataclass(frozen=True, slots=True)
class RouteEntry:
    phase: Phase                    # minimum phase required
    client_kind: str                # "yfinance" | "sec_edgar" | "bigdata" | "fred"
    method: str                     # method name on the resolved client
    requires_consent: bool = False  # INV-GS-040 — explicit Phase 2/3 user opt-in


# Routing table: each (expert, data_type) maps to an ordered preference list.
# Earlier entries win when the active phase satisfies them.
_ROUTING: Final[Mapping[tuple[str, str], tuple[RouteEntry, ...]]] = {
    ("E_FUNDAMENTAL", "ohlcv"):       (RouteEntry("mvp", "yfinance",  "get_ohlcv"),),
    ("E_FUNDAMENTAL", "fundamentals"): (
        RouteEntry("mvp",     "yfinance",  "get_fundamentals"),
        RouteEntry("phase_2", "bigdata",   "bigdata_company_tearsheet", requires_consent=True),
    ),
    ("E_FUNDAMENTAL", "filings"):     (RouteEntry("mvp", "sec_edgar", "get_filings"),),
    ("E_FUNDAMENTAL", "company_facts"): (RouteEntry("mvp", "sec_edgar", "get_company_facts"),),

    ("E_FUND_FLOW", "13f"):          (RouteEntry("mvp", "sec_edgar", "get_13f_holdings"),),
    ("E_FUND_FLOW", "13f_quarterly"): (RouteEntry("mvp", "sec_edgar", "get_filings"),),
    ("E_FUND_FLOW", "13f_holdings"): (RouteEntry("mvp", "sec_edgar", "get_13f_holdings"),),
    ("E_FUND_FLOW", "fund_trends"): (
        RouteEntry("phase_2", "bigdata", "bigdata_company_tearsheet", requires_consent=True),
    ),
    ("E_FUND_FLOW", "holders"):      (RouteEntry("mvp", "yfinance", "get_holders"),),
    ("E_FUND_FLOW", "institutional_holders"): (
        RouteEntry("mvp", "yfinance", "get_holders"),
    ),

    ("E_TIME", "ohlcv"):             (RouteEntry("mvp", "yfinance", "get_ohlcv"),),
    ("E_TIME", "earnings_calendar"): (
        RouteEntry("mvp",     "yfinance", "get_earnings_calendar"),
        RouteEntry("phase_2", "bigdata",  "bigdata_events_calendar", requires_consent=True),
    ),
    ("E_TIME", "dividends"):         (RouteEntry("mvp", "yfinance", "get_dividends"),),

    # Phase 2+ only — no free fallback.
    ("E_NARRATIVE", "search"):       (
        RouteEntry("phase_2", "bigdata", "bigdata_search", requires_consent=True),
    ),
    ("E_ESG", "tearsheet"):          (
        RouteEntry("phase_2", "bigdata", "bigdata_company_tearsheet", requires_consent=True),
    ),
    ("E_MACRO", "macro"):            (
        RouteEntry("phase_2", "fred",    "get_series", requires_consent=False),
        RouteEntry("phase_2", "bigdata", "bigdata_country_tearsheet", requires_consent=True),
    ),
    ("E_GLOBAL_FLOW", "etf"):        (RouteEntry("mvp", "yfinance", "get_ohlcv"),),
    ("E_GLOBAL_FLOW", "factors"):    (
        RouteEntry("phase_2", "bigdata", "bigdata_market_tearsheet", requires_consent=True),
    ),
    ("E_CASCADE", "filings"):        (
        RouteEntry("phase_3", "bigdata", "bigdata_search", requires_consent=True),
    ),
    ("E_CASCADE", "transcripts"):    (
        RouteEntry("phase_3", "bigdata", "bigdata_search", requires_consent=True),
    ),

    # Phase 1C — Macro and commodity research experts. Both run on free
    # yfinance OHLCV; E_COMMODITY_TS additionally uses the public CFTC client.
    ("E_FX_CARRY",     "ohlcv"): (RouteEntry("mvp", "yfinance", "get_ohlcv"),),
    ("E_COMMODITY_TS", "ohlcv"): (RouteEntry("mvp", "yfinance", "get_ohlcv"),),
    ("E_COMMODITY_TS", "cot"):   (RouteEntry("mvp", "cftc",     "fetch_range"),),
}


_PHASE_ORDER: Final[Mapping[Phase, int]] = {"mvp": 0, "phase_2": 1, "phase_3": 2}


def _resolve_phase_from_yaml(yaml_path: Path) -> Phase:
    if not yaml_path.exists():
        return "mvp"
    try:
        import yaml  # noqa: PLC0415 — optional dep guard

        data = yaml.safe_load(yaml_path.read_text()) or {}
    except Exception:
        return "mvp"
    raw = str(data.get("phase", "mvp")).strip().lower()
    if raw not in _PHASE_ORDER:
        return "mvp"
    return raw  # type: ignore[return-value]


@dataclass(slots=True)
class DataRouter:
    clients: dict[str, Any] = field(default_factory=dict)
    consent: set[str] = field(default_factory=set)   # e.g. {"phase_2", "phase_3"}
    budget_yaml: Path = _BUDGET_YAML_DEFAULT

    def register_client(self, kind: str, instance: Any) -> None:
        # WHY: explicit DI — router doesn't import bigdata in MVP setups.
        self.clients[kind] = instance

    def grant_consent(self, phase: Phase) -> None:
        self.consent.add(phase)

    def revoke_consent(self, phase: Phase) -> None:
        self.consent.discard(phase)

    def active_phase(self) -> Phase:
        env = os.environ.get("GLOSTAT_PHASE")
        if env:
            normalized = env.strip().lower()
            if normalized in _PHASE_ORDER:
                return normalized  # type: ignore[return-value]
            raise ConfigError(
                f"INV-GS-039: unknown GLOSTAT_PHASE={env!r} "
                "(allowed: mvp, phase_2, phase_3)"
            )
        return _resolve_phase_from_yaml(self.budget_yaml)

    def route(self, expert: str, data_type: str) -> tuple[Any, str]:
        # Returns (client_instance, method_name). Raises ConfigError on phase/consent gates.
        key = (expert, data_type)
        candidates = _ROUTING.get(key)
        if not candidates:
            raise ConfigError(
                f"INV-GS-039: no route registered for ({expert!r}, {data_type!r})"
            )
        active = self.active_phase()
        active_rank = _PHASE_ORDER[active]

        first_phase_violation: RouteEntry | None = None
        first_consent_violation: RouteEntry | None = None

        for entry in candidates:
            if _PHASE_ORDER[entry.phase] > active_rank:
                first_phase_violation = first_phase_violation or entry
                continue
            if entry.requires_consent and entry.phase not in self.consent:
                first_consent_violation = first_consent_violation or entry
                continue
            client = self.clients.get(entry.client_kind)
            if client is None:
                # WHY: router knows the route exists but caller didn't wire the client —
                # surface a typed error rather than silently falling through.
                raise ConfigError(
                    f"INV-GS-039: route ({expert}, {data_type}) → {entry.client_kind} "
                    "but no client registered. Call DataRouter.register_client first."
                )
            return client, entry.method

        # Nothing matched. Pick the most informative error.
        if first_phase_violation is not None:
            needed = first_phase_violation.phase
            if first_phase_violation.client_kind == "bigdata" and active == "mvp":
                raise ConfigError(
                    f"Bigdata disabled in MVP per INV-GS-036. "
                    f"Route ({expert}, {data_type}) needs phase={needed}."
                )
            raise ConfigError(
                f"INV-GS-039: route ({expert}, {data_type}) needs phase={needed}, "
                f"active={active}."
            )
        if first_consent_violation is not None:
            needed = first_consent_violation.phase
            raise ConfigError(
                f"INV-GS-040: route ({expert}, {data_type}) requires explicit "
                f"{needed} consent. Call DataRouter.grant_consent({needed!r})."
            )
        raise ConfigError(
            f"INV-GS-039: no eligible route for ({expert}, {data_type}) under {active}."
        )


__all__ = ["DataRouter", "Phase", "RouteEntry"]
