from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

# Verdict v1 (PLAN_v0.4 §3.4) — frozen dataclass for hot path; pydantic for boundaries.

Action = Literal["BUY", "HOLD", "SELL"]
ExpertName = Literal[
    "E_FUNDAMENTAL",
    "E_FUNDAMENTAL_KR",
    "E_FUND_FLOW",
    "E_TIME",
    "E_FOREIGN_REVERSAL",
]


@dataclass(frozen=True, slots=True)
class SessionWindow:
    name: str
    open_local: str
    close_local: str
    open_utc: str
    close_utc: str


@dataclass(frozen=True, slots=True)
class MarketMeta:
    mic: str
    name: str
    country: str
    currency: str
    tz: str
    sessions: tuple[SessionWindow, ...]
    settlement_days: int
    fee_bps: float
    tax_bps_buy: float
    tax_bps_sell: float
    tick_size: str
    holidays_calendar: str
    bigdata_mcp_coverage: Literal["HIGH", "MEDIUM", "LOW", "NONE"]
    foreign_access: Literal["open", "registered_only", "stock_connect_only", "restricted"]
    daily_limit_pct: float | None = None

    def all_in_bps(self, side: Literal["buy", "sell"]) -> float:
        tax = self.tax_bps_buy if side == "buy" else self.tax_bps_sell
        return self.fee_bps + tax


@dataclass(frozen=True, slots=True)
class ExpertSignal:
    expert_name: ExpertName
    ticker: str
    direction: Literal["LONG", "SHORT", "NEUTRAL"]
    net_score: float
    confidence: float
    archetype: Literal["impulse", "continuation", "contrarian", "mixed"]
    basis: str
    sources: tuple[str, ...]
    expires_at: datetime
    metadata: tuple[tuple[str, str], ...] = field(default_factory=tuple)


# Output of the Gating composer (MOET A1+A2+A3). Frozen dataclass so callers can
# audit the final weights, applied multipliers, and source signals deterministically.
@dataclass(frozen=True, slots=True)
class ComposedSignal:
    aggregated_score: float
    aggregated_confidence: float
    direction: Literal["LONG", "SHORT", "NEUTRAL"]
    disagreement_weight: float                          # 1.0 = consensus, 0.0 = total split
    per_signal_weights: tuple[tuple[str, float], ...]   # ((expert_name, final_weight), ...)
    applied_anti_herd: bool
    applied_minority_premium: tuple[str, ...]           # expert_names that received the boost
    source_signals: tuple[ExpertSignal, ...]


# Verdict v1 — PLAN_v0.4 §3.4 simplified (3 Expert × US × Swing only)
@dataclass(frozen=True, slots=True)
class Verdict:
    ticker: str                                  # bare ticker (US only, MVP)
    action: Action                               # 5단계 → 3단계 (E1 horizon discipline)
    conviction_w: float                          # [0, 3.5] TITAN W값
    target_price: float | None
    stop_price: float | None
    suggested_size_pct: float
    horizon_days: int                            # explicit, 1-30 (Swing only)
    edge_bps: float
    all_in_bps: float                            # XNAS = 0.6bps fee + 0.24bps SEC sell
    cost_passed: bool                            # INV-GS-001
    expected_pnl_bps: float                      # = upside − current_loss (INV-GS-028)
    disagreement_weight: float                   # [0,1] 1=consensus, 0=split (INV-GS-029)
    contributing_signals: tuple[ExpertSignal, ...]
    next_trigger: str
    evidence_hash: str                           # Merkle leaf (INV-GS-022)
    prompt_versions: tuple[tuple[str, str], ...] # ((expert, sha256), ...) (INV-GS-023)
    git_commit: str
    user_profile_hash: str                       # personal-use audit (INV-GS-024)
    issued_at: datetime
    market: Literal["XNAS", "XNYS"] = "XNAS"

    def __post_init__(self) -> None:
        if not 0.0 <= self.conviction_w <= 3.5:
            raise ValueError(f"conviction_w {self.conviction_w} out of [0, 3.5]")
        if not 1 <= self.horizon_days <= 30:
            raise ValueError(f"horizon_days {self.horizon_days} out of [1, 30] (Swing only)")
        if not 0.0 <= self.disagreement_weight <= 1.0:
            raise ValueError(f"disagreement_weight {self.disagreement_weight} out of [0, 1]")
        if self.action in {"BUY"} and not self.cost_passed:
            raise ValueError("INV-GS-001 violation: BUY emitted with cost_passed=False")
        if not self.prompt_versions:
            raise ValueError("INV-GS-023 violation: prompt_versions empty")
        if not self.evidence_hash:
            raise ValueError("INV-GS-022 violation: evidence_hash empty")


# Pydantic boundary validators — used at API/CLI/MCP edges only.

class ExpertSignalIn(BaseModel):
    model_config = ConfigDict(frozen=True, str_strip_whitespace=True)

    expert_name: ExpertName
    ticker: str = Field(min_length=1, max_length=8)
    direction: Literal["LONG", "SHORT", "NEUTRAL"]
    net_score: float = Field(ge=-3.0, le=3.0)
    confidence: float = Field(ge=0.0, le=1.0)
    archetype: Literal["impulse", "continuation", "contrarian", "mixed"]
    basis: str = Field(max_length=512)
    sources: list[str] = Field(min_length=1)
    expires_at: datetime
    metadata: dict[str, str] = Field(default_factory=dict)

    @field_validator("ticker")
    @classmethod
    def _ticker_uppercase(cls, v: str) -> str:
        if not v.isascii() or not v.replace(".", "").isalnum():
            raise ValueError(f"invalid ticker: {v!r}")
        return v.upper()

    def to_dataclass(self) -> ExpertSignal:
        return ExpertSignal(
            expert_name=self.expert_name,
            ticker=self.ticker,
            direction=self.direction,
            net_score=self.net_score,
            confidence=self.confidence,
            archetype=self.archetype,
            basis=self.basis,
            sources=tuple(self.sources),
            expires_at=self.expires_at,
            metadata=tuple(sorted(self.metadata.items())),
        )


class VerdictIn(BaseModel):
    model_config = ConfigDict(frozen=True, str_strip_whitespace=True)

    ticker: str = Field(min_length=1, max_length=8)
    action: Action
    conviction_w: float = Field(ge=0.0, le=3.5)
    target_price: float | None = None
    stop_price: float | None = None
    suggested_size_pct: float = Field(ge=0.0, le=100.0)
    horizon_days: int = Field(ge=1, le=30)
    edge_bps: float
    all_in_bps: float = Field(ge=0.0)
    cost_passed: bool
    expected_pnl_bps: float
    disagreement_weight: float = Field(ge=0.0, le=1.0)
    contributing_signals: list[ExpertSignalIn] = Field(min_length=1)
    next_trigger: str
    evidence_hash: str = Field(min_length=64, max_length=64)
    prompt_versions: dict[str, str] = Field(min_length=1)
    git_commit: str = Field(min_length=7)
    user_profile_hash: str = Field(min_length=64, max_length=64)
    issued_at: datetime
    market: Literal["XNAS", "XNYS"] = "XNAS"

    def to_dataclass(self) -> Verdict:
        return Verdict(
            ticker=self.ticker,
            action=self.action,
            conviction_w=self.conviction_w,
            target_price=self.target_price,
            stop_price=self.stop_price,
            suggested_size_pct=self.suggested_size_pct,
            horizon_days=self.horizon_days,
            edge_bps=self.edge_bps,
            all_in_bps=self.all_in_bps,
            cost_passed=self.cost_passed,
            expected_pnl_bps=self.expected_pnl_bps,
            disagreement_weight=self.disagreement_weight,
            contributing_signals=tuple(s.to_dataclass() for s in self.contributing_signals),
            next_trigger=self.next_trigger,
            evidence_hash=self.evidence_hash,
            prompt_versions=tuple(sorted(self.prompt_versions.items())),
            git_commit=self.git_commit,
            user_profile_hash=self.user_profile_hash,
            issued_at=self.issued_at,
            market=self.market,
        )


def verdict_to_canonical_json(v: Verdict) -> str:
    payload = asdict(v)
    payload["issued_at"] = v.issued_at.isoformat()
    payload["contributing_signals"] = [
        {**asdict(s), "expires_at": s.expires_at.isoformat()}
        for s in v.contributing_signals
    ]
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)


def verdict_sha256(v: Verdict) -> str:
    return hashlib.sha256(verdict_to_canonical_json(v).encode("utf-8")).hexdigest()
