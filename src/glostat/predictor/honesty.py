from __future__ import annotations

import math
from pathlib import Path
from typing import Final

import yaml

# v1.4.1 — output-honesty helpers (INV-GS-113 + INV-GS-114).
# P8 Statistician + P10 Contrarian Veteran panel synthesis (X+W patch).
# All logic here is presentation-only; the composite predictor itself is
# unchanged. These helpers compute statistical-significance metadata and
# universe-specific honesty footers that the CLI layer renders.

_REPO_ROOT: Final[Path] = Path(__file__).resolve().parents[3]
_MARKETS_YAML: Final[Path] = _REPO_ROOT / "configs" / "markets.yaml"

# AUC SE under H₀ (random AUC = 0.5) — conservative approximation:
#   SE ≈ 1 / sqrt(12 · n)
# Used by P8 in the v1.4.1 panel evaluation. Matches Hanley & McNeil for
# balanced classes within ~10% — close enough for honest disclosure.
_AUC_NULL: Final[float] = 0.5
_SIG_ALPHA: Final[float] = 0.05
_Z_AT_ALPHA_05: Final[float] = 1.96  # two-tailed 5% threshold

_KR_MARKETS: Final[frozenset[str]] = frozenset({"XKRX", "XKOS"})


def auc_standard_error(n: int) -> float:
    # Conservative SE under H₀. Returns +inf for n=0 so z-score is 0.
    if n <= 0:
        return float("inf")
    return 1.0 / math.sqrt(12.0 * n)


def auc_z_score(auc: float, n: int) -> float:
    # Two-tailed z relative to AUC = 0.5 (no discrimination).
    if n <= 0:
        return 0.0
    se = auc_standard_error(n)
    return (auc - _AUC_NULL) / se


def auc_p_value(auc: float, n: int) -> float:
    # Two-tailed p-value. Returns 1.0 for n=0 (fully indistinguishable).
    if n <= 0:
        return 1.0
    z = abs(auc_z_score(auc, n))
    # Φ(z) = 0.5 · (1 + erf(z / √2)); p = 2 · (1 - Φ(z)).
    phi = 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))
    return max(0.0, min(1.0, 2.0 * (1.0 - phi)))


def is_statistically_significant(
    auc: float, n: int, *, alpha: float = _SIG_ALPHA
) -> bool:
    if n <= 0:
        return False
    return auc_p_value(auc, n) < alpha


def format_significance(auc: float, n: int) -> str:
    # Compact tag for the AUC line, e.g. "p=0.31, n.s." or "p<0.001".
    if n <= 0:
        return "no data"
    p = auc_p_value(auc, n)
    if p < 0.001:
        return "p<0.001"
    if p < 0.01:
        return f"p={p:.3f}"
    tag = "n.s." if p >= _SIG_ALPHA else "sig"
    return f"p={p:.2f}, {tag}"


def round_trip_bps(market: str) -> float:
    # Sum of (fee_buy + tax_buy) + (fee_sell + tax_sell). Falls back to 0.0
    # for unknown markets; callers that care should branch on the result.
    raw = _market_raw(market)
    if raw is None:
        return 0.0
    fee = _as_float(raw.get("fee_bps", 0.0))
    tax_buy = _as_float(raw.get("tax_bps_buy", 0.0))
    tax_sell = _as_float(raw.get("tax_bps_sell", 0.0))
    return 2.0 * fee + tax_buy + tax_sell


def _as_float(v: object) -> float:
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        try:
            return float(v)
        except ValueError:
            return 0.0
    return 0.0


def kr_megacap_honesty_note(market: str) -> str | None:
    # P10 Contrarian Veteran W: explicit universe-specific honesty footer.
    # KR megacap (XKRX/XKOS) hindcasts measured AUC ≤ 0.51 across n=3,510
    # in Phase KR M1; surface that to the user so the +Xpp edge is read in
    # the right context. Returns None for non-KR markets.
    if market not in _KR_MARKETS:
        return None
    return (
        "Universe note (KR megacap): Phase KR M1 hindcast measured "
        "AUC <= 0.51 on n=3,510 KOSPI 200 samples — the framework's "
        "discrimination on KR megacap is at the edge of statistical "
        "noise. Treat probability output as a screening prior; "
        "supplement with sector / commodity-cycle analysis (정유, "
        "철강, 화학 cyclicals are not captured by current experts)."
    )


def all_active_signals_are_noise(
    aucs_and_ns: tuple[tuple[float, int], ...],
    *,
    alpha: float = _SIG_ALPHA,
) -> bool:
    # P8 Statistician X6: when every active signal fails the z-test, the
    # composite edge is statistically indistinguishable from base rate.
    # Used by the CLI layer to decide whether to emit the warning footer.
    if not aucs_and_ns:
        return True
    return all(
        not is_statistically_significant(auc, n, alpha=alpha)
        for auc, n in aucs_and_ns
    )


def ci_includes_zero(low_bps: float, high_bps: float) -> bool:
    return low_bps <= 0.0 <= high_bps


# ── internal helpers ───────────────────────────────────────────────────────


def _market_raw(mic: str) -> dict[str, object] | None:
    if not _MARKETS_YAML.exists():
        return None
    try:
        data = yaml.safe_load(_MARKETS_YAML.read_text("utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return None
    markets = data.get("markets", {}) or {}
    raw = markets.get(mic)
    if not isinstance(raw, dict):
        return None
    return raw


__all__ = [
    "all_active_signals_are_noise",
    "auc_p_value",
    "auc_standard_error",
    "auc_z_score",
    "ci_includes_zero",
    "format_significance",
    "is_statistically_significant",
    "kr_megacap_honesty_note",
    "round_trip_bps",
]
