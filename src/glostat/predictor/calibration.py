from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Final

import structlog

# Calibration table: per-thesis (auc, sharpe, n_samples, oos_degradation)
# from cached Phase 1B/1C/1D hindcast reports. Composite weights = sigmoid(Brier).

log: Final = structlog.get_logger(__name__)

_REPO_ROOT: Final[Path] = Path(__file__).resolve().parents[3]
_CACHE_DIR: Final[Path] = _REPO_ROOT / "cache"
_DEFAULT_CALIBRATION_PARQUET: Final[Path] = _CACHE_DIR / "calibration_table.parquet"
_DEFAULT_PERIOD_START: Final[date] = date(2024, 1, 1)
_DEFAULT_PERIOD_END: Final[date] = date(2026, 3, 31)

# Activation thresholds: |auc-0.5| > 0.02 AND n_samples >= 50 → "active".
_DEFAULT_AUC_DELTA: Final[float] = 0.02
_DEFAULT_MIN_SAMPLES: Final[int] = 50
_RANDOM_AUC: Final[float] = 0.50


@dataclass(frozen=True, slots=True)
class ThesisCalibration:
    name: str
    auc: float
    sharpe: float
    n_samples: int
    oos_degradation: float
    period_start: date
    period_end: date

    @property
    def brier_score(self) -> float:
        # WHY: brier ≈ 0.25 * (1 - 2*|auc-0.5|) * sample_penalty.
        # Tiny n lifts Brier toward 0.25 (random) so weight collapses.
        edge = abs(self.auc - 0.5)
        sample_penalty = min(1.0, self.n_samples / 100.0)
        raw = 0.25 * (1.0 - 2.0 * edge)
        return min(0.25, raw + 0.25 * (1.0 - sample_penalty) * 0.5)

    @property
    def directional_bias(self) -> int:
        # AUC > 0.5 informative; AUC < 0.5 anti-informative (flip in composite).
        if self.auc > 0.5 + 1e-9:
            return +1
        if self.auc < 0.5 - 1e-9:
            return -1
        return 0


@dataclass(slots=True)
class CalibrationTable:
    entries: dict[str, ThesisCalibration] = field(default_factory=dict)
    snapshot_path: Path = _DEFAULT_CALIBRATION_PARQUET

    def get(self, name: str) -> ThesisCalibration:
        if name in self.entries:
            return self.entries[name]
        # WHY: fall back to a "random" calibration so a freshly-added thesis
        # without cached hindcast still serializes. is_active() will return False.
        return ThesisCalibration(
            name=name,
            auc=_RANDOM_AUC,
            sharpe=0.0,
            n_samples=0,
            oos_degradation=0.0,
            period_start=_DEFAULT_PERIOD_START,
            period_end=_DEFAULT_PERIOD_END,
        )

    def names(self) -> tuple[str, ...]:
        return tuple(sorted(self.entries.keys()))

    def to_records(self) -> list[dict[str, Any]]:
        return [
            {
                "name": cal.name,
                "auc": cal.auc,
                "sharpe": cal.sharpe,
                "n_samples": cal.n_samples,
                "oos_degradation": cal.oos_degradation,
                "period_start": cal.period_start.isoformat(),
                "period_end": cal.period_end.isoformat(),
                "brier_score": cal.brier_score,
            }
            for cal in self.entries.values()
        ]

    def write_parquet(self, path: Path | None = None) -> Path:
        target = path or self.snapshot_path
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            import polars as pl  # noqa: PLC0415 — optional cold path

            df = pl.DataFrame(self.to_records())
            df.write_parquet(target)
        except ImportError:
            # WHY: never break the predictor over an optional dep. Fall back to JSON
            # alongside the parquet path so downstream `glostat status` can still see it.
            target = target.with_suffix(".json")
            target.write_text(json.dumps(self.to_records(), indent=2, sort_keys=True))
        return target


def is_active(
    cal: ThesisCalibration,
    *,
    auc_delta: float = _DEFAULT_AUC_DELTA,
    min_samples: int = _DEFAULT_MIN_SAMPLES,
) -> bool:
    # WHY: "auc meaningfully above random" + "sample size sufficient" — keeps
    # tiny-n theses from steering composite even if Sharpe looks great.
    if cal.n_samples < min_samples:
        return False
    return abs(cal.auc - 0.5) > auc_delta


# ── Hindcast report loaders ────────────────────────────────────────────────


def _safe_read_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text("utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        log.warning("calibration.read_failed", path=str(path), err=str(exc))
        return None


def _calibration_from_phase1b(
    payload: dict[str, Any], thesis_name: str
) -> ThesisCalibration | None:
    # Phase 1B layout: {"report": {...metrics}, "gate": {...}}
    report = payload.get("report") or payload
    if not isinstance(report, dict):
        return None
    auc = float(report.get("overall_auc", _RANDOM_AUC))
    sharpe = float(report.get("overall_sharpe", 0.0))
    n = int(report.get("n_trades", report.get("n_signals", 0)))
    is_sharpe = float(report.get("is_sharpe", 0.0))
    oos_sharpe = float(report.get("oos_sharpe", 0.0))
    oos_deg = _compute_oos_degradation(is_sharpe, oos_sharpe)
    return ThesisCalibration(
        name=thesis_name, auc=auc, sharpe=sharpe, n_samples=n,
        oos_degradation=oos_deg,
        period_start=_DEFAULT_PERIOD_START, period_end=_DEFAULT_PERIOD_END,
    )


def _calibration_from_phase1c(
    payload: dict[str, Any], thesis_name: str
) -> ThesisCalibration | None:
    # Phase 1C layout: top-level metrics directly (PhaseHindcastReport.dump).
    auc = float(payload.get("overall_auc", _RANDOM_AUC))
    sharpe = float(payload.get("overall_sharpe", 0.0))
    n = int(payload.get("n_trades", payload.get("n_signals", 0)))
    is_sharpe = float(payload.get("is_sharpe", 0.0))
    oos_sharpe = float(payload.get("oos_sharpe", 0.0))
    oos_deg = _compute_oos_degradation(is_sharpe, oos_sharpe)
    return ThesisCalibration(
        name=thesis_name, auc=auc, sharpe=sharpe, n_samples=n,
        oos_degradation=oos_deg,
        period_start=_DEFAULT_PERIOD_START, period_end=_DEFAULT_PERIOD_END,
    )


def _compute_oos_degradation(is_sharpe: float, oos_sharpe: float) -> float:
    if is_sharpe <= 0.0:
        return 1.0
    return max(0.0, 1.0 - (oos_sharpe / is_sharpe))


# Phase 1D markdown reports — parse the small comparison table into calibration.
# WHY: phase1d emits markdown only; rather than re-running the costly hindcast,
# extract numbers from the comparison report. Fragile but bounded.
_PHASE1D_LABEL_RULES: Final[tuple[tuple[str, str, str], ...]] = (
    # (rule_kind, needle, key) — rule_kind ∈ {contains, eq, startswith}.
    ("contains",   "sharpe (overall)", "sharpe"),
    ("eq",         "sharpe is",        "sharpe_is"),
    ("eq",         "sharpe oos",       "sharpe_oos"),
    ("contains",   "auc (overall)",    "auc"),
    ("startswith", "traded",           "traded"),
    ("startswith", "actionable",       "actionable"),
)


def _classify_phase1d_label(label: str) -> str | None:
    # Map a row label (lower-case, asterisk-stripped) to its metric key. Bare
    # exact matches avoid e.g. "actionable" capturing "hit_rate_actionable".
    bare = label.strip()
    for kind, needle, key in _PHASE1D_LABEL_RULES:
        if kind == "contains" and needle in bare:
            return key
        if kind == "eq" and bare == needle:
            return key
        if kind == "startswith" and bare.startswith(needle):
            return key
    return None


def _parse_phase1d_md_row(line: str, column: int) -> tuple[str, float] | None:
    stripped = line.strip()
    if not stripped.startswith("|"):
        return None
    cells = [c.strip() for c in stripped.strip("|").split("|")]
    if len(cells) < column + 1:
        return None
    label = cells[0].lower().strip("*")
    try:
        value = float(cells[column].replace("%", "").replace("+", "").replace(",", ""))
    except ValueError:
        return None
    key = _classify_phase1d_label(label)
    return None if key is None else (key, value)


def _calibration_from_phase1d_md(
    md_text: str, thesis_name: str, column: int
) -> ThesisCalibration | None:
    fields: dict[str, float] = {}
    for line in md_text.splitlines():
        parsed = _parse_phase1d_md_row(line, column)
        if parsed is not None:
            label, value = parsed
            fields[label] = value
    auc = fields.get("auc")
    sharpe = fields.get("sharpe")
    n_actionable = fields.get("actionable")
    n_traded = fields.get("traded")
    is_sharpe = fields.get("sharpe_is")
    oos_sharpe = fields.get("sharpe_oos")
    # Prefer actionable count (signal events generated, pre-cost) — closer to
    # the v1.0 framing where cost gate is downstream of the calibration.
    n = int(n_actionable) if n_actionable is not None else (
        int(n_traded) if n_traded is not None else None
    )
    if auc is None or sharpe is None or n is None:
        return None
    if is_sharpe is None or oos_sharpe is None:
        oos_deg = 0.0
    else:
        oos_deg = _compute_oos_degradation(is_sharpe, oos_sharpe)
    return ThesisCalibration(
        name=thesis_name, auc=auc, sharpe=sharpe, n_samples=n,
        oos_degradation=oos_deg,
        period_start=_DEFAULT_PERIOD_START, period_end=_DEFAULT_PERIOD_END,
    )


# Map: thesis_name → (relative_path, loader, extra_args)
# WHY: declarative table keeps the load loop short and easy to extend.
_PHASE_SOURCES: Final[tuple[tuple[str, str, str, dict[str, Any]], ...]] = (
    ("E_FUNDAMENTAL",     "phase1b/e_fundamental_report.json",     "phase1b", {}),
    ("E_TIME",            "phase1b/e_time_report.json",            "phase1b", {}),
    ("E_FUND_FLOW",       "phase1b/e_fund_flow_report.json",       "phase1b", {}),
    ("E_SECTOR_ROTATION", "phase1b/e_sector_rotation_report.json", "phase1b", {}),
    ("E_PEAD",            "phase1b/e_pead_report.json",            "phase1b", {}),
    ("E_FOMC_DRIFT",      "phase1b/e_fomc_drift_report.json",      "phase1b", {}),
    ("E_INSIDER_CLUSTER", "phase1b/e_insider_cluster_report.json", "phase1b", {}),
    ("E_COMMODITY_TS",    "hindcast/phase1c_commodity_ts_report.json", "phase1c", {}),
    ("E_FX_CARRY",        "hindcast/phase1c_fx_carry_report.json",     "phase1c", {}),
    # v1.2 L1 — KR-specific calibration. Distinguishes E_TIME (US) from E_TIME_KR.
    # The phase_kr loader writes phase1b-shaped payload so the existing parser
    # ingests these unmodified.
    ("E_FUNDAMENTAL_KR",  "hindcast/phase_kr/e_fundamental_kr_report.json",  "phase1b", {}),
    ("E_TIME_KR",         "hindcast/phase_kr/e_time_kr_report.json",         "phase1b", {}),
    ("E_FOREIGN_REVERSAL_KR", "hindcast/phase_kr/e_foreign_reversal_report.json", "phase1b", {}),
)

# Phase 1D — markdown-only, parse columns from comparison table.
_PHASE1D_MD: Final[str] = "hindcast/phase1d/phase1d_comparison.md"


def load_calibration(cache_dir: Path | None = None) -> CalibrationTable:
    base = cache_dir or _CACHE_DIR
    table = CalibrationTable(snapshot_path=base / "calibration_table.parquet")
    for thesis, rel_path, kind, _extra in _PHASE_SOURCES:
        payload = _safe_read_json(base / rel_path)
        if payload is None:
            continue
        loader = (
            _calibration_from_phase1b if kind == "phase1b"
            else _calibration_from_phase1c
        )
        cal = loader(payload, thesis)
        if cal is not None:
            table.entries[thesis] = cal
    # Phase 1D — markdown comparison file.
    md_path = base / _PHASE1D_MD
    if md_path.exists():
        md_text = md_path.read_text("utf-8")
        # column 1 = E7 Funding Carry, column 2 = E9 Foreign Reversal
        e7 = _calibration_from_phase1d_md(md_text, "E_FUNDING_CARRY", column=1)
        e9 = _calibration_from_phase1d_md(md_text, "E_FOREIGN_REVERSAL", column=2)
        if e7 is not None:
            table.entries["E_FUNDING_CARRY"] = e7
        if e9 is not None:
            table.entries["E_FOREIGN_REVERSAL"] = e9
    # v1.2 L1 — Phase KR overrides phase1d when both exist. The KR-specific
    # hindcast covers a wider universe + window and is the authoritative
    # calibration for the live composite.
    kr_rev = table.entries.get("E_FOREIGN_REVERSAL_KR")
    if kr_rev is not None:
        table.entries["E_FOREIGN_REVERSAL"] = ThesisCalibration(
            name="E_FOREIGN_REVERSAL", auc=kr_rev.auc, sharpe=kr_rev.sharpe,
            n_samples=kr_rev.n_samples, oos_degradation=kr_rev.oos_degradation,
            period_start=kr_rev.period_start, period_end=kr_rev.period_end,
        )
    # v1.1 K1: backfill thesis with no cached hindcast report from the synthetic
    # baseline so the live predictor has at least the v0.6 calibration to lean
    # on. This is documented in docs/CALIBRATION.md and re-derived during the
    # quarterly recalibration (INV-GS-105). Without this backfill an E_TIME or
    # E_FUNDAMENTAL prediction would carry weight=0 and the composite would
    # collapse to the base rate prior — see also K1 brief.
    _backfill_from_synthetic(table)
    return table


def _backfill_from_synthetic(table: CalibrationTable) -> None:
    synthetic = synthetic_calibration_for_mock()
    for name, cal in synthetic.entries.items():
        if name not in table.entries:
            table.entries[name] = cal


def synthetic_calibration_for_mock() -> CalibrationTable:
    # WHY: --mock CLI runs need a deterministic calibration table even when
    # cache/ is empty. Mirrors the actual archived numbers so the printed output
    # looks like the real thing.
    table = CalibrationTable()
    table.entries.update({
        "E_FUNDAMENTAL": ThesisCalibration(
            "E_FUNDAMENTAL", auc=0.55, sharpe=0.40, n_samples=120,
            oos_degradation=0.20,
            period_start=_DEFAULT_PERIOD_START, period_end=_DEFAULT_PERIOD_END,
        ),
        # v1.1 K1 — KR fundamentals bootstrapped at AUC=0.5, n=0 (no hindcast yet).
        # Composite weight = 0 until calibration table is rebuilt.
        "E_FUNDAMENTAL_KR": ThesisCalibration(
            "E_FUNDAMENTAL_KR", auc=0.50, sharpe=0.0, n_samples=0,
            oos_degradation=0.0,
            period_start=_DEFAULT_PERIOD_START, period_end=_DEFAULT_PERIOD_END,
        ),
        "E_TIME": ThesisCalibration(
            "E_TIME", auc=0.52, sharpe=0.30, n_samples=200,
            oos_degradation=0.15,
            period_start=_DEFAULT_PERIOD_START, period_end=_DEFAULT_PERIOD_END,
        ),
        "E_FUND_FLOW": ThesisCalibration(
            "E_FUND_FLOW", auc=0.48, sharpe=-0.10, n_samples=80,
            oos_degradation=0.50,
            period_start=_DEFAULT_PERIOD_START, period_end=_DEFAULT_PERIOD_END,
        ),
        "E_SECTOR_ROTATION": ThesisCalibration(
            "E_SECTOR_ROTATION", auc=0.470, sharpe=-0.478, n_samples=174,
            oos_degradation=1.0,
            period_start=_DEFAULT_PERIOD_START, period_end=_DEFAULT_PERIOD_END,
        ),
        "E_PEAD": ThesisCalibration(
            "E_PEAD", auc=0.586, sharpe=0.629, n_samples=298,
            oos_degradation=1.156,
            period_start=_DEFAULT_PERIOD_START, period_end=_DEFAULT_PERIOD_END,
        ),
        "E_FOMC_DRIFT": ThesisCalibration(
            "E_FOMC_DRIFT", auc=0.357, sharpe=-1.340, n_samples=135,
            oos_degradation=1.0,
            period_start=_DEFAULT_PERIOD_START, period_end=_DEFAULT_PERIOD_END,
        ),
        "E_INSIDER_CLUSTER": ThesisCalibration(
            "E_INSIDER_CLUSTER", auc=0.339, sharpe=0.782, n_samples=11,
            oos_degradation=0.0,
            period_start=_DEFAULT_PERIOD_START, period_end=_DEFAULT_PERIOD_END,
        ),
        "E_COMMODITY_TS": ThesisCalibration(
            "E_COMMODITY_TS", auc=0.489, sharpe=0.139, n_samples=517,
            oos_degradation=1.0,
            period_start=_DEFAULT_PERIOD_START, period_end=_DEFAULT_PERIOD_END,
        ),
        "E_FX_CARRY": ThesisCalibration(
            "E_FX_CARRY", auc=0.400, sharpe=-1.533, n_samples=135,
            oos_degradation=1.0,
            period_start=_DEFAULT_PERIOD_START, period_end=_DEFAULT_PERIOD_END,
        ),
        "E_FUNDING_CARRY": ThesisCalibration(
            "E_FUNDING_CARRY", auc=0.5052, sharpe=-0.2314, n_samples=2921,
            oos_degradation=4.5741,
            period_start=_DEFAULT_PERIOD_START, period_end=_DEFAULT_PERIOD_END,
        ),
        # v1.1 K1 — Phase 1D live hindcast (n=424, AUC=0.4667, Sharpe=0.5834).
        # AUC < 0.5 → directional_bias=-1; composite flips the score.
        "E_FOREIGN_REVERSAL": ThesisCalibration(
            "E_FOREIGN_REVERSAL", auc=0.4667, sharpe=0.5834, n_samples=424,
            oos_degradation=0.0,
            period_start=_DEFAULT_PERIOD_START, period_end=_DEFAULT_PERIOD_END,
        ),
        # v1.2 L1 — KR-specific E_TIME calibration. Bootstrapped at AUC=0.5,
        # n=0 so the composite weight=0 until a phase_kr hindcast lands.
        # Distinct from US E_TIME (AUC=0.52) so the predictor can look up the
        # right cell when scoring KR tickers.
        "E_TIME_KR": ThesisCalibration(
            "E_TIME_KR", auc=0.50, sharpe=0.0, n_samples=0,
            oos_degradation=0.0,
            period_start=_DEFAULT_PERIOD_START, period_end=_DEFAULT_PERIOD_END,
        ),
        # v1.2 L2 — KR insider cluster (DART elestock). n=0 placeholder until
        # a KR insider hindcast measures AUC. Composite weight = 0 until then.
        "E_INSIDER_KR": ThesisCalibration(
            "E_INSIDER_KR", auc=0.50, sharpe=0.0, n_samples=0,
            oos_degradation=0.0,
            period_start=_DEFAULT_PERIOD_START, period_end=_DEFAULT_PERIOD_END,
        ),
        # v1.3 M2 — KR macro (ECOS BoK). n=0 placeholder until a KR macro
        # hindcast that includes E_MACRO_KR runs. Composite weight = 0 until
        # then; the signal still surfaces in contributing_signals so the user
        # sees the macro picture (raw_score, basis), just with weight=0.
        "E_MACRO_KR": ThesisCalibration(
            "E_MACRO_KR", auc=0.50, sharpe=0.0, n_samples=0,
            oos_degradation=0.0,
            period_start=_DEFAULT_PERIOD_START, period_end=_DEFAULT_PERIOD_END,
        ),
        # v1.4 N2 — KR short-selling (KRX). n=0 placeholder; weight=0 until a
        # dedicated short-selling hindcast runs. Surfaces in contributing
        # signals so the user sees the short-balance picture.
        "E_SHORT_SELLING_KR": ThesisCalibration(
            "E_SHORT_SELLING_KR", auc=0.50, sharpe=0.0, n_samples=0,
            oos_degradation=0.0,
            period_start=_DEFAULT_PERIOD_START, period_end=_DEFAULT_PERIOD_END,
        ),
        # v1.4 N2 — KR intraday flow (Naver + KIS overlay). n=0 placeholder.
        "E_INTRADAY_FLOW_KR": ThesisCalibration(
            "E_INTRADAY_FLOW_KR", auc=0.50, sharpe=0.0, n_samples=0,
            oos_degradation=0.0,
            period_start=_DEFAULT_PERIOD_START, period_end=_DEFAULT_PERIOD_END,
        ),
        # v1.5 P6 — KR cyclical-sector fundamentals (EV/EBITDA + commodity
        # cycle). n=0 placeholder; weight=0 until a phase_kr_cyclical hindcast
        # measures predictive AUC for cyclical universe.
        "E_FUNDAMENTAL_KR_CYCLICAL": ThesisCalibration(
            "E_FUNDAMENTAL_KR_CYCLICAL", auc=0.50, sharpe=0.0, n_samples=0,
            oos_degradation=0.0,
            period_start=_DEFAULT_PERIOD_START, period_end=_DEFAULT_PERIOD_END,
        ),
        # v1.5 P6 — KR refining commodity-momentum (WTI + crack spread).
        # Refining-universe-only; n=0 placeholder.
        "E_COMMODITY_INDEX_KR": ThesisCalibration(
            "E_COMMODITY_INDEX_KR", auc=0.50, sharpe=0.0, n_samples=0,
            oos_degradation=0.0,
            period_start=_DEFAULT_PERIOD_START, period_end=_DEFAULT_PERIOD_END,
        ),
    })
    return table


__all__ = [
    "CalibrationTable",
    "ThesisCalibration",
    "is_active",
    "load_calibration",
    "synthetic_calibration_for_mock",
]
