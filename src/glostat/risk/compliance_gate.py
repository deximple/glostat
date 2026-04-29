from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final, Literal, NoReturn

import structlog

# E9 / INV-GS-024: Telegram broadcast permanently forbidden. Personal-use only.
# Per-jurisdiction disclaimer templates rendered every verdict.

log: Final = structlog.get_logger(__name__)

Jurisdiction = Literal["KR", "US", "EU", "JP", "TW", "HK", "DEFAULT"]


class ComplianceError(RuntimeError):
    """Raised when a forbidden compliance action is attempted (broadcast, mass email, ...)."""


@dataclass(frozen=True, slots=True)
class ComplianceContext:
    user_profile_hash: str   # SHA256 of (user_id, jurisdiction, license tier)
    jurisdiction: Jurisdiction
    personal_use_only: bool = True
    license_tier: Literal["personal", "research", "commercial"] = "personal"

    def is_personal(self) -> bool:
        return self.personal_use_only and self.license_tier == "personal"


@dataclass(frozen=True, slots=True)
class DisclaimerTemplate:
    jurisdiction: Jurisdiction
    body: str

    def render(self, *, ticker: str, action: str, issued_at: str) -> str:
        return self.body.format(ticker=ticker, action=action, issued_at=issued_at)


_DISCLAIMERS: Final[dict[Jurisdiction, DisclaimerTemplate]] = {
    "KR": DisclaimerTemplate(
        jurisdiction="KR",
        body=(
            "[GLOSTAT — 개인 사용 전용 / Personal-use only]\n"
            "본 verdict({ticker} {action}, {issued_at})는 자본시장법상 "
            "투자자문/투자권유가 아니며, 사용자 본인의 자기책임 하에 참고용으로만 활용됩니다.\n"
            "타인에게 전송, 게시, 또는 채널 broadcast는 금지됩니다 (INV-GS-024)."
        ),
    ),
    "US": DisclaimerTemplate(
        jurisdiction="US",
        body=(
            "[GLOSTAT — Personal Use Only]\n"
            "Verdict {ticker} {action} issued {issued_at} is not investment advice; "
            "not an offer or solicitation. For your personal use only. "
            "Redistribution, broadcast, or syndication is prohibited (INV-GS-024)."
        ),
    ),
    "EU": DisclaimerTemplate(
        jurisdiction="EU",
        body=(
            "[GLOSTAT — Personal Use Only / MiFID II Article 24 disclaimer]\n"
            "Verdict {ticker} {action} ({issued_at}) is informational research output only. "
            "It does not constitute investment advice or an investment recommendation. "
            "Redistribution prohibited under RavenPack data licensing terms "
            "(INV-GS-024, INV-GS-035)."
        ),
    ),
    "JP": DisclaimerTemplate(
        jurisdiction="JP",
        body=(
            "[GLOSTAT — 個人利用限定]\n"
            "本verdict ({ticker} {action}, {issued_at})は投資助言ではなく、"
            "ユーザー自身の判断と責任で参照されるものです。"
            "第三者への配信および掲示は禁止されています (INV-GS-024)。"
        ),
    ),
    "TW": DisclaimerTemplate(
        jurisdiction="TW",
        body=(
            "[GLOSTAT — 僅供個人使用]\n"
            "本verdict ({ticker} {action}, {issued_at})不構成投資建議。"
            "嚴禁轉發或廣播至他人 (INV-GS-024)。"
        ),
    ),
    "HK": DisclaimerTemplate(
        jurisdiction="HK",
        body=(
            "[GLOSTAT — Personal use only / SFC compliance]\n"
            "Verdict {ticker} {action} ({issued_at}) is not regulated investment advice. "
            "No redistribution permitted (INV-GS-024)."
        ),
    ),
    "DEFAULT": DisclaimerTemplate(
        jurisdiction="DEFAULT",
        body=(
            "[GLOSTAT — Personal Use Only]\n"
            "Verdict {ticker} {action} issued {issued_at} is informational only and not "
            "investment advice. Redistribution forbidden (INV-GS-024)."
        ),
    ),
}


def assert_personal_use(ctx: ComplianceContext) -> None:
    if not ctx.is_personal():
        log.error(
            "compliance.violation",
            license_tier=ctx.license_tier,
            personal_use_only=ctx.personal_use_only,
            user_profile_hash=ctx.user_profile_hash[:12],
        )
        raise ComplianceError(
            f"INV-GS-024: GLOSTAT MVP is personal-use only "
            f"(got license_tier={ctx.license_tier}, personal_use_only={ctx.personal_use_only})"
        )


def disclaimer_for(jurisdiction: Jurisdiction) -> DisclaimerTemplate:
    return _DISCLAIMERS.get(jurisdiction, _DISCLAIMERS["DEFAULT"])


def broadcast_telegram(
    *,
    ctx: ComplianceContext,
    chat_ids: Sequence[str],
    message: str,
) -> NoReturn:
    log.error(
        "compliance.broadcast_blocked",
        attempted_recipients=len(chat_ids),
        message_preview=message[:64],
        user_profile_hash=ctx.user_profile_hash[:12],
    )
    raise ComplianceError(
        "INV-GS-024: broadcast_telegram is permanently forbidden. "
        "GLOSTAT MVP is personal-use only — no syndication, no broadcast."
    )


def mass_email(
    *,
    ctx: ComplianceContext,
    recipients: Sequence[str],
    subject: str,
) -> NoReturn:
    log.error(
        "compliance.mass_email_blocked",
        attempted_recipients=len(recipients),
        subject=subject,
        user_profile_hash=ctx.user_profile_hash[:12],
    )
    raise ComplianceError(
        "INV-GS-024: mass_email is permanently forbidden. "
        "GLOSTAT MVP is personal-use only."
    )
