from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Final

import structlog

# Calibration table — derives per-thesis (auc, sharpe, n_samples, oos_degradation)
# from cached Phase 1B/1C/1D hindcast reports. The composite predictor weights
# each thesis's contribution by these calibration metrics: a thesis with AUC=0.59
# and n=300 carries more weight than AUC=0.50 with n=11.

log: Final = structlog.get_logger(__name__)

_REPO_ROOT: Final[Path] = Path(__file__).resolve().parents[3]
_CACHE_DIR: Final[Path] = _REPO_ROOT / "cache"
_DEFAULT_CALIBRATION_PARQUET: Final[Path] = _CACHE_DIR / "calibration_table.parquet"
_DEFAULT_PERIOD_START: Final[date] = date(2024, 1, 1)
_DEFAULT_PERIOD_END: Final[date] = date(2026, 3, 31)

# Activation threshold — a thesis is "active" when AUC is meaningfully above
# random AND sample size is sufficient. WHY: with n=11 even 0.78 Sharpe is
# noise (E_INSIDER_CLUSTER); we still record it but down-weight via Brier.
_DEFAULT_AUC_DELTA: Final[float] = 0.02   # |auc - 0.5| > 0.02
_DEFAULT_MIN_SAMPLES: Final[int] = 50

# Empirical defaults for theses that haven't run a cached hindcast yet — set
# to AUC=0.50 (random), n=0 → effectively skipped by `is_active`. Avoids
# silent crashes when wrappers reference theses not present in cache.
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
        # WHY: derive Brier-like score from AUC. AUC=0.5 → Brier=0.25 (max
        # uncertainty), AUC>0.5 → Brier shrinks toward 0. Calibration sample
        # size penalizes small-n theses (n=10 still gets full credit otherwise).
        # Mapping: brier ≈ 0.25 * (1 - 2*|auc-0.5|) * sample_penalty
        edge = abs(self.auc - 0.5)
        sample_penalty = min(1.0, self.n_samples / 100.0)
        # When sample is tiny we lift Brier toward the maximum so the weight collapses.
        raw = 0.25 * (1.0 - 2.0 * edge)
        return min(0.25, raw + 0.25 * (1.0 - sample_penalty) * 0.5)

    @property
    def directional_bias(self) -> int:
        # WHY: a thesis with AUC > 0.5 is informative; with AUC < 0.5 the
        # signal is anti-informative (flip the direction). Composite uses this.
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
def _calibration_from_phase1d_md(
    md_text: str, thesis_name: str, column: int
) -> ThesisCalibration | None:
    auc: float | None = None
    sharpe: float | None = None
    n: int | None = None
    is_sharpe: float | None = None
    oos_sharpe: float | None = None
    for line in md_text.splitlines():
        stripped = line.strip()
        if not stripped.startswith("|"):
            continue
        cells = [c.strip() for c in stripped.strip("|").split("|")]
        if len(cells) < column + 1:
            continue
        label = cells[0].lower()
        try:
            value = float(cells[column].replace("%", "").replace("+", ""))
        except ValueError:
            continue
        if "sharpe (overall)" in label:
            sharpe = value
        elif label == "sharpe is":
            is_sharpe = value
        elif label == "sharpe oos":
            oos_sharpe = value
        elif "auc (overall)" in label:
            auc = value
        elif label.startswith("traded"):
            n = int(value)
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
    return table


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
        "E_FOREIGN_REVERSAL": ThesisCalibration(
            "E_FOREIGN_REVERSAL", auc=0.4667, sharpe=0.5834, n_samples=418,
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
