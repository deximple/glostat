"""Generate a SYNTHETIC VKOSPI history CSV for harness validation.

WHY this exists:

  E_VKOSPI_MOOD_KR's calibration measurement requires daily VKOSPI close
  prices. KRX UI export is the recommended path (see docs/VKOSPI_SETUP.md)
  but requires manual operator action. This script generates a deterministic
  synthetic series with realistic statistical properties so the v1.10.8
  hindcast harness can be validated end-to-end without operator intervention.

  The output is NOT a measurement of the true Lee/Son/Lee 2024 thesis edge.
  Any AUC / Sharpe number produced from this CSV is a HARNESS VALIDATION
  result only. To measure the actual thesis, the operator must export real
  VKOSPI history from KRX and re-run the hindcast.

Statistical properties matched to KRX (2009) "변동성지수(VKOSPI) 해설 및
실증분석", 금융정보연구:
  - Mean level ~18 (typical KOSPI 200 IV band 12-30)
  - AR(1) = -0.168 (Table 14: 1-day mean reversion)
  - AR(2) = -0.087 (Table 14: 2-day mean reversion)
  - AR(3) = -0.146 (Table 14: 3-day mean reversion)
  - Occasional fear spikes (>30) clustered around stress events
  - No leading-indicator power vs KOSPI 200 (cross-correlation lag-0 only)

Usage:
  uv run python scripts/generate_synthetic_vkospi.py [start_date] [end_date]

Defaults: 2024-01-02 .. 2026-03-29 (matches kr-vkospi-hindcast defaults).

Output: cache/vkospi_history_synthetic.csv
"""
from __future__ import annotations

import math
import random
import sys
from datetime import date, timedelta
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
_OUT_DIR = _REPO_ROOT / "cache"
_OUT_FILE = _OUT_DIR / "vkospi_history_synthetic.csv"

# Calibrated to KRX 2009 Table 14 + Table 15 statistics.
_MEAN_LEVEL: float = 18.5
_AR1: float = -0.168
_AR2: float = -0.087
_AR3: float = -0.146
_NOISE_SCALE: float = 0.85
_SPIKE_PROB: float = 0.012      # ~1.2% of days = stress spike
_SPIKE_MULT: float = 1.45       # spike close = 1.45× prior close
_RNG_SEED: int = 20260502       # deterministic for reproducibility


def generate(start: date, end: date) -> list[tuple[date, float]]:
    rng = random.Random(_RNG_SEED)
    bars: list[tuple[date, float]] = []
    closes: list[float] = []
    cur = start
    while cur <= end:
        if cur.weekday() >= 5:   # skip weekends
            cur += timedelta(days=1)
            continue
        # Drift toward mean (mean-reverting OU process with discrete AR terms).
        if len(closes) < 3:
            level = _MEAN_LEVEL + rng.gauss(0, _NOISE_SCALE)
        else:
            ar_term = (
                _AR1 * (closes[-1] - _MEAN_LEVEL)
                + _AR2 * (closes[-2] - _MEAN_LEVEL)
                + _AR3 * (closes[-3] - _MEAN_LEVEL)
            )
            noise = rng.gauss(0, _NOISE_SCALE)
            level = _MEAN_LEVEL + ar_term + noise
            # Occasional fear spikes (cluster days with vol blow-up).
            if rng.random() < _SPIKE_PROB:
                level = closes[-1] * _SPIKE_MULT
        # Clip to realistic VKOSPI band [8, 60].
        level = max(8.0, min(60.0, level))
        closes.append(level)
        bars.append((cur, round(level, 2)))
        cur += timedelta(days=1)
    return bars


def write_csv(path: Path, bars: list[tuple[date, float]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# SYNTHETIC VKOSPI series for harness validation only.",
        "# NOT a real measurement — see scripts/generate_synthetic_vkospi.py.",
        "# Statistical properties calibrated to KRX (2009) Table 14:",
        f"#   mean={_MEAN_LEVEL}, AR(1)={_AR1}, AR(2)={_AR2}, AR(3)={_AR3}",
        "# Replace with real KRX export per docs/VKOSPI_SETUP.md before",
        "# claiming any thesis-edge result.",
        "date,close",
    ]
    for d, c in bars:
        lines.append(f"{d.isoformat()},{c}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main(argv: list[str]) -> int:
    start = date.fromisoformat(argv[1]) if len(argv) > 1 else date(2024, 1, 2)
    end = date.fromisoformat(argv[2]) if len(argv) > 2 else date(2026, 3, 29)
    if end <= start:
        print("end must be after start", file=sys.stderr)
        return 2
    bars = generate(start, end)
    write_csv(_OUT_FILE, bars)
    closes = [c for _, c in bars]
    mean = sum(closes) / len(closes)
    var = sum((c - mean) ** 2 for c in closes) / len(closes)
    sd = math.sqrt(var)
    print(f"Generated {len(bars)} bars")
    print(f"  range: {bars[0][0]}..{bars[-1][0]}")
    print(f"  mean : {mean:.2f}")
    print(f"  sd   : {sd:.2f}")
    print(f"  min  : {min(closes):.2f}")
    print(f"  max  : {max(closes):.2f}")
    print(f"  → {_OUT_FILE}")
    print()
    print("WARNING: This is SYNTHETIC data. Hindcast results from this CSV are")
    print("a harness validation only, NOT a measurement of the real thesis edge.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
