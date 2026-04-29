# Contributing to GLOSTAT

Thanks for considering a contribution. GLOSTAT is a small, opinionated
research framework, and the rules below exist to keep its core property —
"hindcast results are reproducible, and the framework cannot silently lie to
you" — intact across PRs.

## Ground rules

- **Read the post-mortem first.** `docs/post_mortem/SPRINT5_FAIL_post_mortem.md`
  explains what failed and why. Most useful PRs strengthen the testing
  discipline that surfaced that failure rather than re-trying the same
  thesis.
- **Files ≤ 400 lines, 800 hard cap.** Split before they grow.
- **No silent invariant weakening.** Anything that touches `INV-GS-*` must
  call it out in the PR description.
- **Frozen dataclasses internally, Pydantic at boundaries** (CLI / MCP /
  HTTP).
- **`from __future__ import annotations`** at the top of every module.

## Dev loop

```bash
uv sync --extra dev
uv run pytest -q                          # full suite, ~500 tests
uv run pytest -q -m invariant             # only INV-GS-*
uv run ruff check .
uv run ruff format --check .
uv run mypy
```

Network-touching tests are gated behind `NETWORK_TESTS=1`. Default CI runs
without it.

## Adding a new Expert

Templates live in `docs/EXAMPLES.md`. The shape:

1. Create `src/glostat/experts/e_<name>.py` exposing an `E<Name>Expert` class
   with `async def compute(self, ticker: str, ts: datetime) -> ExpertSignal`.
2. Score model: `direction` ∈ `{LONG, SHORT, NEUTRAL}`, `confidence` ∈
   `[0, 1]`, `score` ∈ `[-3, +3]`, `sources` listing the snapshot leaves it
   read.
3. Add to `src/glostat/experts/__init__.py`.
4. Wire any new data calls through `DataRouter`, never directly.
5. Add a unit test under `tests/test_e_<name>.py` using the existing fixtures
   pattern (see `tests/test_e_fundamental.py`).
6. Add a hindcast smoke test that runs ≥ 30 days on ≥ 3 tickers and asserts
   the report is well-formed (does *not* assert PASS — that is the gate's
   job).

## Adding a new data source client

1. Create `src/glostat/data/<source>_client.py` with an async API that
   accepts an injected `SnapshotBroker` and persists every response.
2. If rate-limited, self-throttle (see `yfinance_client.py` 5 req/s pattern).
3. If the source needs auth, fail fast at construction time
   (`GLOSTAT_<SOURCE>_API_KEY` env var) — never default-skip with a stub
   credential.
4. Add a routing entry in `src/glostat/data/data_router.py` with the
   appropriate `Phase` (`mvp` for free, `phase_2` for paid). Set
   `requires_consent=True` for paid sources.
5. Tests: a `MockTransport`-style unit test (see `tests/test_sec_edgar_client.py`)
   plus a network test marked `@pytest.mark.network`.

## Extending the Hindcast harness

The harness is `src/glostat/replay/validation_harness.py`. Sensible
extensions:

- Add a metric to `replay/metrics.py` and wire it through `HindcastReport`.
- Add a tunable to `PassCriteria` (always with a documented default — *never*
  weaken an existing default in the same PR).
- Add a new IS/OOS split strategy by extending `HindcastSplit`.

If you find yourself wanting to add a "pass anyway" override, stop. That is
exactly the path the post-mortem warns against.

## Adding an INV-GS invariant

1. Reserve the next free `INV-GS-NNN` ID and add it to
   `configs/invariants.yaml` with `summary`, `enforcement`, `source`, and
   (if applicable) `deferred_to: <phase>`.
2. Add a unit test under `tests/test_invariants*.py` decorated
   `@pytest.mark.invariant` that asserts both the positive case (invariant
   holds) and the negative case (violation is detected and rejected).
3. If the invariant is enforced at construction time of a frozen dataclass,
   the test should attempt construction and assert the expected exception.
4. Mention the invariant ID in the PR title or body.

## Test conventions

- Pytest with `asyncio_mode = "auto"` (already configured).
- Markers: `@pytest.mark.invariant`, `@pytest.mark.slow`,
  `@pytest.mark.network`. Use them.
- Prefer fixtures over re-instantiating clients in every test.
- Snapshot broker tests should use a `tmp_path` root, not a shared one.
- Hindcast tests should use deterministic seeds via `derive_seed(...)`.

## Commit + PR style

- Conventional commits (`feat:`, `fix:`, `refactor:`, `docs:`, `test:`,
  `chore:`).
- PR description should answer: what changed, what invariant it touches (if
  any), how it was tested, and whether it weakens any pass criterion.
- Keep PRs reviewable — under ~400 lines of diff is the sweet spot.
- CI must be green.

## What we will probably reject

- Silent loosening of `PassCriteria` defaults.
- Adding a paid data source without a phase gate or without
  `requires_consent=True`.
- Bypassing `SnapshotBroker.save_snapshot` for "performance".
- Removing the broadcast guard, even for "test mode".
- Net-new files exceeding 400 lines.

## Questions

Open an issue with the `question` label, or use the `alpha_thesis` template
if you want to discuss a new thesis to validate using the framework.
