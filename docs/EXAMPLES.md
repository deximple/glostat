# Extending GLOSTAT

Practical recipes for plugging your own thesis, data source, invariant, or
metric into the framework. Read the
[post-mortem](post_mortem/SPRINT5_FAIL_post_mortem.md) first — it explains
the failure mode the framework is designed to detect.

---

## Example 1 — Build a new Expert from scratch

A minimal `Expert` returns an `ExpertSignal` for a `(ticker, ts)` pair.
Direction ∈ `{LONG, SHORT, NEUTRAL}`, confidence ∈ `[0, 1]`, score in
roughly `[-3, +3]`, and `sources` lists the snapshot leaves it consumed.

`src/glostat/experts/e_momentum.py`:

```python
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Final

from glostat.core.errors import ExpertSkipError
from glostat.core.types import ExpertSignal
from glostat.data.data_router import DataRouter

_LOOKBACK_DAYS: Final[int] = 60
_DIRECTION_THRESHOLD: Final[float] = 1.0
_SCORE_CLIP: Final[float] = 3.0


@dataclass(frozen=True, slots=True)
class MomentumScore:
    z_return: float
    n_obs: int

    @property
    def direction(self) -> str:
        if self.z_return > _DIRECTION_THRESHOLD:
            return "LONG"
        if self.z_return < -_DIRECTION_THRESHOLD:
            return "SHORT"
        return "NEUTRAL"

    @property
    def confidence(self) -> float:
        return min(1.0, abs(self.z_return) / _SCORE_CLIP)


class EMomentumExpert:
    expert_name = "E_MOMENTUM"

    def __init__(self, router: DataRouter) -> None:
        self._router = router

    async def compute(self, ticker: str, ts: datetime) -> ExpertSignal:
        ohlcv = await self._router.fetch(
            "E_MOMENTUM", "ohlcv",
            ticker=ticker, start=ts - timedelta(days=_LOOKBACK_DAYS), end=ts,
        )
        if ohlcv is None or len(ohlcv) < _LOOKBACK_DAYS // 2:
            raise ExpertSkipError(
                expert="E_MOMENTUM", ticker=ticker, reason="insufficient OHLCV history",
            )
        # toy z-score of trailing return — replace with your real model
        rets = [c["close"] / c["open"] - 1 for c in ohlcv]
        mean = sum(rets) / len(rets)
        var = sum((r - mean) ** 2 for r in rets) / len(rets)
        z = mean / max(var ** 0.5, 1e-9)
        score = MomentumScore(z_return=z, n_obs=len(rets))

        return ExpertSignal(
            expert_name="E_MOMENTUM",
            ticker=ticker,
            direction=score.direction,
            net_score=max(min(z, _SCORE_CLIP), -_SCORE_CLIP),
            confidence=score.confidence,
            archetype="continuation" if score.direction != "NEUTRAL" else "mixed",
            basis=f"z_return={z:+.2f} over {score.n_obs} bars",
            sources=tuple(c["snapshot_id"] for c in ohlcv if "snapshot_id" in c),
            expires_at=ts + timedelta(days=30),
        )
```

Wire it through `src/glostat/experts/__init__.py`:

```python
from glostat.experts.e_momentum import EMomentumExpert, MomentumScore

__all__ = [
    # … existing entries …
    "EMomentumExpert",
    "MomentumScore",
]
```

Add a unit test under `tests/test_e_momentum.py` that:
1. Constructs the expert with a mocked `DataRouter`,
2. Asserts the signal shape on a happy-path fixture,
3. Asserts `ExpertSkipError` on insufficient history,
4. Asserts determinism (same inputs → same `net_score`).

---

## Example 2 — Run a hindcast with a custom universe

Feed a real `pipeline` (a callable that returns a `Verdict` for a given day)
into `Hindcast`. The harness handles per-day iteration, IS/OOS splitting,
metric aggregation, and pass-criteria evaluation.

```python
from datetime import date, datetime, UTC
from pathlib import Path

from glostat.data.snapshot_broker import SnapshotBroker
from glostat.data.data_router import DataRouter
from glostat.experts import EFundamentalExpert, ETimeExpert
from glostat.replay.validation_harness import (
    Hindcast,
    HindcastSplit,
    PassCriteria,
)
from glostat.verdict_builder import build_verdict

UNIVERSE = ("AAPL", "MSFT", "NVDA", "GOOGL", "META")  # your call
BROKER  = SnapshotBroker(root=Path("./snapshots"))
ROUTER  = DataRouter()  # MVP phase by default

def pipeline_for_day(day: date, ticker: str):
    """Synthesise a verdict for (day, ticker). Plug your real Experts here."""
    ts = datetime.combine(day, datetime.min.time(), tzinfo=UTC)
    experts = [EFundamentalExpert(router=ROUTER), ETimeExpert(router=ROUTER)]
    signals = [await_safely(e.compute(ticker, ts)) for e in experts]
    return build_verdict(
        ticker=ticker, signals=signals, market_meta=load_xnas(),
        ts=ts, prompt_versions={}, horizon_days=30,
    )

split = HindcastSplit.from_range(date(2026, 1, 1), date(2026, 4, 1), ratio=0.7)
report = Hindcast(pipeline=pipeline_for_day, universe=UNIVERSE).run(
    start_date=split.in_sample_start,
    end_date=split.out_sample_end,
)

verdict = PassCriteria().evaluate(report)
print(f"Sharpe={report.sharpe:.3f}  AUC={report.auc:.3f}  → {verdict}")
```

If `verdict` is `FAIL`, **do not loosen the criteria**. The whole point of
the gate is that you have to fix the thesis, not the test.

---

## Example 3 — Define a new INV-GS invariant

Suppose your new `EMomentumExpert` should never receive more than 25% of the
final composite weight. That is an invariant (`INV-GS-041`).

Step 1 — register it in `configs/invariants.yaml`:

```yaml
INV-GS-041:
  summary: "E_MOMENTUM weight ≤ 25% in composite"
  enforcement: gating_cap
  source: "user-defined / contributor PR"
```

Step 2 — enforce it in code. For weight caps, the cleanest place is the
gating composer (`src/glostat/gating/composer.py` if present, or wherever
your composite weight assignment lives):

```python
_MOMENTUM_WEIGHT_CAP: Final[float] = 0.25

def _apply_caps(weights: dict[str, float]) -> dict[str, float]:
    capped = dict(weights)
    if capped.get("E_MOMENTUM", 0.0) > _MOMENTUM_WEIGHT_CAP:
        capped["E_MOMENTUM"] = _MOMENTUM_WEIGHT_CAP
    # renormalise so weights sum to 1
    total = sum(capped.values())
    return {k: v / total for k, v in capped.items()}
```

Step 3 — write the unit test under `tests/test_invariants.py`:

```python
import pytest


@pytest.mark.invariant
def test_inv_gs_041_e_momentum_weight_cap() -> None:
    weights = {"E_MOMENTUM": 0.5, "E_FUNDAMENTAL": 0.5}
    capped = _apply_caps(weights)
    assert capped["E_MOMENTUM"] <= 0.25 + 1e-9
    assert abs(sum(capped.values()) - 1.0) < 1e-9
```

Mention `INV-GS-041` in the PR title.

---

## Example 4 — Plug a new data source via DataRouter

Free-tier Stooq daily bars as a yfinance fallback.

Step 1 — write the client at `src/glostat/data/stooq_client.py` (~150
lines), accepting an injected `SnapshotBroker`, self-throttling, and
persisting every response to the broker.

```python
from __future__ import annotations

import asyncio
import csv
import io
from datetime import date
from typing import Any, Final

import httpx

from glostat.data.snapshot_broker import SnapshotBroker, SnapshotKey

_RATE_LIMIT_PER_SEC: Final[int] = 5
_BASE_URL: Final[str] = "https://stooq.com/q/d/l/"


class StooqClient:
    def __init__(self, snapshot_broker: SnapshotBroker, *, transport=None) -> None:
        self._broker = snapshot_broker
        self._client = httpx.AsyncClient(transport=transport, timeout=10.0)
        self._sem = asyncio.Semaphore(_RATE_LIMIT_PER_SEC)

    async def get_ohlcv(
        self, ticker: str, start: date, end: date,
    ) -> list[dict[str, Any]]:
        async with self._sem:
            r = await self._client.get(
                _BASE_URL,
                params={"s": f"{ticker.lower()}.us", "i": "d",
                        "d1": start.strftime("%Y%m%d"),
                        "d2": end.strftime("%Y%m%d")},
            )
            r.raise_for_status()
            rows = list(csv.DictReader(io.StringIO(r.text)))
        key = SnapshotKey(
            uaid=f"XNAS.{ticker}",
            edge_type="ohlcv",
            ts_utc=__import__("datetime").datetime.combine(end, __import__("datetime").time()),
            tool="stooq.daily",
            params_canon=f'{{"start":"{start}","end":"{end}"}}',
        )
        record = self._broker.save_snapshot(key, rows)
        return [{**row, "snapshot_id": record.leaf.leaf_hash} for row in rows]
```

Step 2 — register it in `data_router.py`:

```python
_ROUTING: Final[Mapping[tuple[str, str], tuple[RouteEntry, ...]]] = {
    # … existing entries …
    ("E_MOMENTUM", "ohlcv"): (
        RouteEntry("mvp", "yfinance", "get_ohlcv"),
        RouteEntry("mvp", "stooq",    "get_ohlcv"),  # fallback
    ),
}
```

Step 3 — in your bootstrap (CLI / library entry point):

```python
router.register_client("stooq", StooqClient(snapshot_broker=broker))
```

Step 4 — tests with an `httpx.MockTransport` (see
`tests/test_sec_edgar_client.py` for the pattern), plus one
`@pytest.mark.network` smoke test guarded by `NETWORK_TESTS=1`.

If Stooq turned paid tomorrow, you would change the `RouteEntry` from
`mvp` to `phase_2` and set `requires_consent=True`. The framework would
then refuse to call it without a `configs/budget.yaml` opt-in — which is
the whole point of the phase-gating layer.

---

## Where to look next

- `tests/test_e_fundamental.py` — full Expert test pattern.
- `tests/test_sec_edgar_client.py` — `MockTransport` HTTP client testing.
- `tests/test_invariants.py` — invariant test conventions.
- `tests/test_hindcast.py` — hindcast harness wiring for a real pipeline.
- `src/glostat/replay/kill_criteria.py` — automatic shutdown on
  invariant violation (`INV-GS-033`).
- `docs/research/snapshot_broker_design.md` — broker design rationale and
  Merkle-leaf format.
