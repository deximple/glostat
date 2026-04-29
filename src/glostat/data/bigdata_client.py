from __future__ import annotations

import json
import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Final, Literal

import structlog

from glostat.core.errors import ConfigError

# Wrapper for 6 Bigdata MCP tools.
# Sprint 0: structure + budget tracking. MCP wiring lands in Sprint 1.
# v0.6: gated behind phase >= phase_2 (INV-GS-036).

log: Final = structlog.get_logger(__name__)

_BUDGET_YAML_DEFAULT: Final = Path(__file__).resolve().parents[3] / "configs" / "budget.yaml"


def _resolve_phase(*, budget_yaml: Path | None = None) -> str:
    # WHY: env var wins over file so tests can flip phase per-process without YAML mutation.
    env_phase = os.environ.get("GLOSTAT_PHASE")
    if env_phase:
        return env_phase.strip().lower()
    yaml_path = budget_yaml or _BUDGET_YAML_DEFAULT
    if not yaml_path.exists():
        return "mvp"
    try:
        import yaml  # noqa: PLC0415 — keeps top-level cost zero when unused

        data = yaml.safe_load(yaml_path.read_text()) or {}
    except Exception:
        # WHY: phase resolution must never crash callers; default safely to MVP.
        return "mvp"
    raw = data.get("phase", "mvp")
    return str(raw).strip().lower()


def assert_phase_2_or_later(*, budget_yaml: Path | None = None) -> None:
    # WHY: hard block at code level — INV-GS-036 demands MVP cannot reach RavenPack.
    phase = _resolve_phase(budget_yaml=budget_yaml)
    if phase == "mvp":
        raise ConfigError(
            "Bigdata disabled in MVP per INV-GS-036. "
            "Set GLOSTAT_PHASE=phase_2 + activate budget consent."
        )
    if phase not in {"phase_2", "phase_3"}:
        raise ConfigError(
            f"Unknown GLOSTAT_PHASE={phase!r}. Expected one of: mvp, phase_2, phase_3."
        )

ToolName = Literal[
    "find_companies",
    "bigdata_company_tearsheet",
    "bigdata_country_tearsheet",
    "bigdata_market_tearsheet",
    "bigdata_search",
    "bigdata_events_calendar",
]

SearchMode = Literal["fast", "smart"]

_TOOL_TTL: Final[Mapping[ToolName, timedelta]] = {
    "find_companies":             timedelta(days=365),  # entity_id permanent (INV-GS-002)
    "bigdata_company_tearsheet":  timedelta(hours=1),
    "bigdata_country_tearsheet":  timedelta(hours=6),
    "bigdata_market_tearsheet":   timedelta(minutes=15),
    "bigdata_search":             timedelta(hours=1),
    "bigdata_events_calendar":    timedelta(hours=24),
}

_FAST_QUOTA_PCT: Final[float] = 0.70
_SMART_QUOTA_PCT: Final[float] = 0.30


@dataclass(slots=True)
class BigdataToolCall:
    tool: ToolName
    params: dict[str, Any]
    requested_at: datetime
    search_mode: SearchMode | None = None
    cost_units: float = 1.0

    def cache_key(self) -> str:
        canonical = json.dumps({"tool": self.tool, "params": self.params},
                               sort_keys=True, separators=(",", ":"))
        return canonical


@dataclass(slots=True)
class BigdataBudget:
    monthly_call_cap: int
    monthly_smart_cap: int
    used_calls: int = 0
    used_smart: int = 0
    rejected_calls: int = 0
    by_tool: dict[ToolName, int] = field(default_factory=dict)

    def can_allocate(self, call: BigdataToolCall) -> bool:
        if self.used_calls >= self.monthly_call_cap:
            return False
        smart_overflow = (
            call.search_mode == "smart" and self.used_smart >= self.monthly_smart_cap
        )
        return not smart_overflow

    def reserve(self, call: BigdataToolCall) -> None:
        if not self.can_allocate(call):
            self.rejected_calls += 1
            raise BudgetExceededError(
                f"Bigdata budget exhausted: tool={call.tool} mode={call.search_mode}"
            )
        self.used_calls += 1
        if call.search_mode == "smart":
            self.used_smart += 1
        self.by_tool[call.tool] = self.by_tool.get(call.tool, 0) + 1

    @property
    def fast_share(self) -> float:
        non_smart = self.used_calls - self.used_smart
        return non_smart / self.used_calls if self.used_calls else 0.0


class BudgetExceededError(RuntimeError):
    """Bigdata MCP monthly budget exceeded — Sprint 0 cost audit produces caps."""


class BigdataClient:
    def __init__(
        self,
        *,
        budget: BigdataBudget,
        cache_dir: str = "cache/bigdata",
    ) -> None:
        self._budget = budget
        self._cache_dir = cache_dir
        self._snapshot_broker: object | None = None  # injected lazily (Sprint 1)

    @property
    def budget(self) -> BigdataBudget:
        return self._budget

    def attach_snapshot_broker(self, broker: object) -> None:
        self._snapshot_broker = broker

    # ── 6 tool surface ──────────────────────────────────────────────────────
    # Sprint 0: stubs raise NotImplementedError.
    # Sprint 1: wire via Anthropic MCP client (`mcp__..._bigdata_*`).

    async def find_companies(
        self, *, query: str, max_results: int = 10
    ) -> list[dict[str, Any]]:
        assert_phase_2_or_later()
        call = BigdataToolCall(
            tool="find_companies",
            params={"query": query, "max_results": max_results},
            requested_at=_utcnow(),
            cost_units=0.5,
        )
        self._budget.reserve(call)
        raise NotImplementedError("MCP wired in S1: find_companies")

    async def bigdata_company_tearsheet(
        self,
        *,
        rp_entity_id: str,
        company_type: Literal["Public", "Private"] = "Public",
        period: Literal["quarter", "annual"] = "quarter",
    ) -> dict[str, Any]:
        assert_phase_2_or_later()
        call = BigdataToolCall(
            tool="bigdata_company_tearsheet",
            params={
                "rp_entity_id": rp_entity_id,
                "company_type": company_type,
                "period": period,
            },
            requested_at=_utcnow(),
            cost_units=2.0,
        )
        self._budget.reserve(call)
        raise NotImplementedError("MCP wired in S1: bigdata_company_tearsheet")

    async def bigdata_country_tearsheet(
        self, *, country_code: str, period: str = "quarter"
    ) -> dict[str, Any]:
        assert_phase_2_or_later()
        call = BigdataToolCall(
            tool="bigdata_country_tearsheet",
            params={"country_code": country_code, "period": period},
            requested_at=_utcnow(),
            cost_units=2.0,
        )
        self._budget.reserve(call)
        raise NotImplementedError("MCP wired in S1 / Phase 2: bigdata_country_tearsheet")

    async def bigdata_market_tearsheet(
        self, *, asset_class: str = "equity"
    ) -> dict[str, Any]:
        assert_phase_2_or_later()
        call = BigdataToolCall(
            tool="bigdata_market_tearsheet",
            params={"asset_class": asset_class},
            requested_at=_utcnow(),
            cost_units=1.5,
        )
        self._budget.reserve(call)
        raise NotImplementedError("MCP wired in S1 / Phase 2: bigdata_market_tearsheet")

    async def bigdata_search(
        self,
        *,
        request: dict[str, Any],
        search_mode: SearchMode = "fast",
    ) -> dict[str, Any]:
        assert_phase_2_or_later()
        call = BigdataToolCall(
            tool="bigdata_search",
            params={"request": request},
            requested_at=_utcnow(),
            search_mode=search_mode,
            cost_units=3.0 if search_mode == "smart" else 1.0,
        )
        self._budget.reserve(call)
        raise NotImplementedError("MCP wired in S1: bigdata_search")

    async def bigdata_events_calendar(
        self, *, start_date: str, end_date: str, exchanges: list[str] | None = None
    ) -> dict[str, Any]:
        assert_phase_2_or_later()
        call = BigdataToolCall(
            tool="bigdata_events_calendar",
            params={
                "start_date": start_date,
                "end_date": end_date,
                "exchanges": sorted(exchanges) if exchanges else None,
            },
            requested_at=_utcnow(),
            cost_units=1.0,
        )
        self._budget.reserve(call)
        raise NotImplementedError("MCP wired in S1: bigdata_events_calendar")

    # ── helpers ─────────────────────────────────────────────────────────────

    def ttl_for(self, tool: ToolName) -> timedelta:
        return _TOOL_TTL[tool]

    def quota_targets(self) -> dict[SearchMode, float]:
        return {"fast": _FAST_QUOTA_PCT, "smart": _SMART_QUOTA_PCT}


def _utcnow() -> datetime:
    return datetime.now(tz=__import__("datetime").timezone.utc)
