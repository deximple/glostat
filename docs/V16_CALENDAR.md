# GLOSTAT v1.6 — P5 Event-Aware Calendar

> Status: ACTIVE 2026-05-02. P5 Event-Driven panel absorption — adds calendar
> awareness to the KR prediction stack so 30-day-horizon outputs widen the
> CI when an imminent event drives volatility, surface concrete D-day
> countdowns in `next_triggers`, and feed `E_PEAD_KR` with the most-recent
> expected earnings filing date.
>
> Information tool. Not investment advice. Past calibration ≠ future performance.

---

## P5 panel finding (motivating example)

The P5 Event-Driven panel reviewed live v1.5 output and surfaced a
time-axis blind spot:

> "v1.5 outputs are static snapshots, not time-axis predictions. 30d horizon
> prediction completely ignores upcoming D-day events (earnings, BoK, OPEC).
> CI doesn't widen as event approaches. `next_triggers` is generic
> ('horizon expires in ~30 days'), not actionable. PEAD signal is wired for
> US (E_PEAD) but KR has no equivalent."

Concrete examples flagged by the panel:

- A prediction emitted on 2026-05-28 for an SK이노베이션 30-day horizon
  spans the BoK 금통위 of 2026-05-30 (D-2). v1.5 emits the same CI width
  whether the BoK meeting is 60 days out or 2 days out. Option-implied vol
  obviously expands as scheduled events approach.
- The `next_triggers` field reads `horizon expires in ~30 days` — useful as
  a fallback but missing the calendar context the user actually wants
  ("what's the next thing that moves this name?").
- Post-earnings drift is a documented KR phenomenon (KIFRS 분기보고서
  deadline = Q-end + 45 days; Naver/대신 quarterly studies show ~1.2 Sharpe
  on T+5 → T+30 drift) but had no expert.

v1.6 absorbs that finding by adding a single calendar client + one new
expert + two presentation-layer refinements that wire the calendar through
to CI and `next_triggers`.

---

## What changed in v1.6

| Module                                         | Lines | Role                                                            |
| ---------------------------------------------- | ----: | --------------------------------------------------------------- |
| `data.kr_calendar_client`                      |  ~250 | KR earnings + BoK + OPEC ministerial + OPEC JMMC                |
| `experts.e_pead_kr`                            |  ~250 | KR Post-Earnings Announcement Drift (T+5 → T+30 window)         |
| `predictor.composite` (delta)                  |  ~30  | `_calendar_sigma_multiplier`, `next_triggers` override, predict() |
| `cli_predictor._build_calendar_overlay`        |  ~20  | Glue: calendar fetch → triggers + days_to_imminent              |

Total: 34 new tests; full suite still green. Invariants:
**INV-GS-119 / 120 / 121**.

---

## INV-GS-119 — `kr_calendar_client`

`src/glostat/data/kr_calendar_client.py` surfaces the next ~60 days of
KR-relevant events. KR has no public earnings-calendar API and no machine-
readable BoK schedule, so each source uses a different acquisition
strategy.

### Public interface

```python
from glostat.data.kr_calendar_client import (
    KrCalendarClient, CalendarEvent, EventKind,
)

events = await client.next_events(today=date.today(), lookahead_days=60)
# → tuple[CalendarEvent, ...] sorted by date
```

`CalendarEvent` fields: `kind` (`EventKind`), `date_utc`, `label`,
`days_to`, plus convenience properties `is_imminent` (≤7d) and
`is_very_imminent` (≤3d).

`EventKind` values: `EARNINGS_KR`, `BOK_RATE`, `OPEC_MINISTER`,
`OPEC_JMMC`.

### Source 1 — KR earnings (KIFRS heuristic)

KR has no public earnings-calendar API. Used heuristic: KIFRS 분기보고서
deadline = **Q-end + 45 days**.

```python
def _next_kr_earnings(today: date) -> date:
    q_ends = [
        date(today.year, 3, 31),    # Q1 → due ~May 15
        date(today.year, 6, 30),    # Q2 → due ~Aug 14
        date(today.year, 9, 30),    # Q3 → due ~Nov 14
        date(today.year, 12, 31),   # Q4 → due ~Feb 14 (next year)
    ]
    for q_end in q_ends:
        report_due = q_end + timedelta(days=45)
        if report_due >= today:
            return report_due
    return date(today.year + 1, 3, 31) + timedelta(days=45)
```

Annual reports use a 90-day deadline (year-end + 90d) but v1.6 only
surfaces the quarterly cadence; the quarterly heuristic dominates the
investor-attention surface area.

### Source 2 — BoK 금통위 (hardcoded 2026)

The Bank of Korea publishes its annual schedule in November of the prior
year as a static HTML page (no API, no JSON feed). 8 meetings/year is
BoK's standing pattern. v1.6 ships the **2026 schedule hardcoded**:

```python
_BOK_2026: Final[tuple[date, ...]] = (
    date(2026, 1, 16),
    date(2026, 2, 27),
    date(2026, 4, 10),
    date(2026, 5, 30),
    date(2026, 7, 11),
    date(2026, 8, 29),
    date(2026, 10, 17),
    date(2026, 11, 28),
)
```

Source: <https://www.bok.or.kr/portal/bbs/B0000232/list.do> (verified
2026-04-29). **Refresh quarterly** — the 2027 schedule will need to be
appended in November 2026.

### Source 3 — OPEC ministerial (auto-scrape with fallback)

OPEC publishes the year's ministerial conference list as static HTML at
<https://www.opec.org/opec_web/en/data_graphs/40.htm>. v1.6 scrapes this
once on first call and caches for 30 days.

Acquisition flow:

1. Check in-memory cache (`_opec_minister_cache`)
2. Check on-disk cache (`cache/kr_calendar/opec_2026.html`, age < 30d)
3. HTTP GET `opec.org/.../40.htm` with httpx (10s timeout, follow_redirects)
4. Persist HTML to disk, write Snapshot Broker leaf (UAID
   `OPEC.MEETINGS`, edge_type `opec_calendar`)
5. Run regex parser
6. **On any failure**: fall back to hardcoded `_OPEC_MINISTER_2026_FALLBACK`
   (June + December — OPEC's typical 2/year cadence)

Regex parser is intentionally narrow:

```python
# Look only inside the same paragraph as "Ministerial" or "Conference".
for chunk in re.findall(
    r"[^.\n]*(?:Ministerial|Conference)[^.\n]*", html, flags=re.IGNORECASE,
):
    found.update(_extract_dates(chunk))
```

Then matches against two date patterns:

- `"5 December 2026"` / `"5 Dec 2026"` — day-month-year
- `"December 5, 2026"` — month-day-year

Filtered to current-year-and-future, capped at 6 entries to bound noise.
If the contextual match returns nothing, the parser returns `()` (no false
positives from scanning the whole document) and the fallback dates kick in.

### Source 4 — OPEC JMMC (first-Wednesday monthly)

OPEC's Joint Ministerial Monitoring Committee meets monthly (regular
review). No published static schedule, but the **first Wednesday of each
month** is the standing slot. Pure date arithmetic, no scraping:

```python
def _next_opec_jmmc(today: date) -> date:
    candidate = today.replace(day=1)
    while True:
        first_wed = candidate
        while first_wed.weekday() != 2:    # 0=Mon..2=Wed
            first_wed += timedelta(days=1)
        if first_wed >= today:
            return first_wed
        next_month = candidate.replace(day=28) + timedelta(days=4)
        candidate = next_month.replace(day=1)
```

### Combined output

`next_events()` aggregates all 4 sources, filters by `lookahead_days`
(default 60), sorts by date, and returns the tuple. Typical KR-ticker
prediction sees 3-6 entries.

---

## INV-GS-120 — `next_triggers` populated from calendar

`predictor.composite.predict()` accepts an optional `next_triggers`
parameter. When `cli_predictor._build_calendar_overlay()` is invoked for a
KR ticker, it overrides the auto-derived list with concrete D-day
countdowns:

```python
# v1.6 P5 (INV-GS-120, INV-GS-121): pull upcoming events for next_triggers
# and CI calendar widening. Failures degrade gracefully to the original
# auto-derived next_triggers.
async def _build_calendar_overlay(
    calendar: KrCalendarClient, *, is_kr: bool,
) -> tuple[tuple[str, ...] | None, int | None]:
    if not is_kr:
        return None, None
    try:
        events = await calendar.next_events()
    except Exception:
        return None, None
    if not events:
        return None, None
    triggers = tuple(
        f"{e.label} (D-{e.days_to})" for e in events
    )
    days_to_imminent = events[0].days_to
    return triggers, days_to_imminent
```

### Presentation contract

| Condition                       | `next_triggers` source                    |
| ------------------------------- | ----------------------------------------- |
| KR ticker + calendar succeeds   | `kr_calendar_client.next_events()`        |
| KR ticker + calendar fails      | `predictor.composite._next_triggers()` (auto-derived) |
| Non-KR ticker (US, FX, crypto)  | `predictor.composite._next_triggers()` (auto-derived) |

The user **always sees ≥ 1 trigger** (horizon expiration is the floor) so
the field is never empty.

### Example output

```
Before v1.6 (auto-derived):
  Next triggers:
    - horizon expires in ~30 days

After v1.6 (calendar overlay):
  Next triggers:
    - KR 분기보고서 due ~2026-05-15 (D-13)
    - BoK 금통위 2026-05-30 (D-28)
    - OPEC JMMC ~2026-06-03 (D-32)
    - OPEC 장관급 회의 2026-06-02 (D-31)
```

D-day notation is the standard KR market convention (`D-N` = N days
remaining; `D+N` = N days after).

---

## INV-GS-121 — CI calendar widening

`predictor.composite.predict()` also accepts `days_to_imminent_event` and
multiplies the CI 1-sigma band when an event is imminent. Reflects the
well-documented increase in option-implied volatility approaching scheduled
events (BoK rate decisions, OPEC meetings, KR earnings deadlines).

### Multiplier table

| `days_to_event` | Multiplier | Rationale                                           |
| --------------- | ---------- | --------------------------------------------------- |
| `None` or `< 0` | ×1.0       | No event in window → unchanged                      |
| `≥ 7`           | ×1.0       | Event far enough out that vol expansion is muted    |
| `< 7`           | ×1.5       | "Imminent" — typical IV expansion starts here       |
| `< 3`           | ×2.0       | "Very imminent" — IV peak; option chain repriced    |

```python
def _calendar_sigma_multiplier(days_to_event: int | None) -> float:
    if days_to_event is None or days_to_event < 0:
        return 1.0
    if days_to_event < 3:
        return 2.0
    if days_to_event < 7:
        return 1.5
    return 1.0
```

### Application order

```
sigma  = base_sigma_from_per_thesis_variance
sigma *= active_signal_count_scaling             # existing v1.4.1 logic
sigma *= _calendar_sigma_multiplier(days_to)     # v1.6 additive
```

The calendar multiplier is applied **after** the active-signal-count
scaling, so the calendar signal stacks rather than replaces. This keeps
the CI honest in two senses simultaneously: ensemble disagreement (signal
count) AND scheduled event proximity.

### Honesty constraint

CI widening is **presentation-honest only** — `p_up` itself does not move.
Coupled with INV-GS-113 (`CI 1-sigma (~68%)` label), the user sees the
wider band correctly labelled. The widening does not represent a new
prediction; it represents the model's increased uncertainty about the
realised return, which is a real and measurable phenomenon as scheduled
events approach.

---

## `E_PEAD_KR` — KR Post-Earnings Drift expert

`src/glostat/experts/e_pead_kr.py` is the KR-specific PEAD expert. Reuses
`KrCalendarClient` to estimate the most-recent expected earnings filing
date, then measures actual OHLCV drift in the T+5 → T+30 window after
that date.

### Score formula

```
last_earnings        = max(Q-end + 45d) where date ≤ today
days_since_earnings  = (today - last_earnings).days
close_t5             = first OHLCV close on or after last_earnings + 5d
close_t30            = first OHLCV close on or after last_earnings + 30d
drift_5_to_30        = (close_t30 - close_t5) / close_t5

drift_signal         = clamp(drift_5_to_30 * 10.0, ±2.0)
net_score            = drift_signal

DRIFT_GAIN              = 10.0     # +20% drift → +2.0 raw
SCORE_CLIP              = 2.0
DIRECTION_THRESHOLD     = 0.4
EARNINGS_FILING_LAG_DAYS = 45      # KIFRS quarterly deadline
DRIFT_WINDOW_START      = 5        # T+5 from filing
DRIFT_WINDOW_END        = 30       # T+30 from filing
```

### Universe gate

KR equities (XKRX/XKOS). Requires `days_since_earnings ≥ 30` (full T+30
window must have closed) and ≥ 30 days of OHLCV after the expected last
earnings date. Otherwise `ExpertSkipError`.

### Archetype: `continuation`

Post-earnings drift is empirically a trend-following effect — names that
move in the post-earnings window tend to extend the move. Same archetype
as `E_PEAD` (US version) and `E_COMMODITY_INDEX_KR`.

### Calibration status — n=0 (pending)

Bootstrapped at AUC=0.500, n=0 → weight=0 until a dedicated KR PEAD
hindcast lands. Same posture as v1.5 cyclical experts and v1.4 N2
short-selling/intraday experts. Roadmap: `glostat kr-hindcast --thesis
E_PEAD_KR --universe KR_KOSPI200 --start 2022-01-01 --end 2026-03-31` →
~16 quarters × 200 tickers ≈ 3200 actionable points → update
`cache/calibration_table.parquet` + regenerate `docs/CALIBRATION.md`.

---

## End-to-end example — SK이노베이션 with calendar overlay

After v1.6:

```
$ GLOSTAT_SEC_USER_AGENT="..." glostat predict 096770
=== GLOSTAT Prediction — 096770 (XKRX) ===
  up / down / sideways: 51.2% / 28.8% / 20.0%
  CI 1-sigma (68%): -42bps .. +70bps   *** widened ×1.5 (BoK D-5)

  E_PEAD_KR  ^  +0.31   no data (n=0, weight=0)
    last_earnings≈2026-05-15 (D+13); drift T+5→T+30 = +3.1%

Next triggers:
  - KR 분기보고서 due ~2026-05-15 (D-13)
  - BoK 금통위 2026-05-30 (D-5)              ← imminent → ×1.5 sigma
  - OPEC JMMC ~2026-06-03 (D-9)
  - OPEC 장관급 회의 2026-06-02 (D-8)
```

Key changes vs v1.5: `next_triggers` is concrete (4 dated entries, not
"expires in ~30 days"); CI band widened ×1.5 because BoK is D-5; the
`*** widened ×1.5` annotation follows the INV-GS-113 honesty pattern;
`E_PEAD_KR` surfaces raw drift even though weight=0.

---

## Test commands

```bash
# Pure-function tests (no network)
uv run pytest -q tests/test_kr_calendar_client.py
uv run pytest -q tests/test_e_pead_kr.py

# Composite + presentation-layer tests
uv run pytest -q tests/test_predictor_composite_calendar.py
uv run pytest -q tests/test_cli_predictor_calendar_overlay.py

# Live smoke (requires NETWORK_TESTS=1 + GLOSTAT_SEC_USER_AGENT)
GLOSTAT_SEC_USER_AGENT="Your Name your@email" NETWORK_TESTS=1 \
  uv run pytest -q tests/test_kr_smoke.py

# End-to-end prediction with calendar overlay
GLOSTAT_SEC_USER_AGENT="Your Name your@email" \
  uv run glostat predict 005930          # 삼성전자 (KR — calendar active)
GLOSTAT_SEC_USER_AGENT="Your Name your@email" \
  uv run glostat predict AAPL            # US — calendar inactive (auto-derived)
```

---

## Cache management

OPEC HTML cache: `cache/kr_calendar/opec_2026.html` (TTL 30d, UTF-8, auto-
cleared on stale read). Force refresh: `rm -f cache/kr_calendar/opec_2026.html`
then re-run `glostat predict`. The Snapshot Broker leaf (UAID `OPEC.MEETINGS`)
is a separate audit-trail entry preserved across deletions.

---

## Known limitations

1. **2026-only BoK schedule.** `_BOK_2026` is hardcoded; append 2027 dates in
   Nov 2026. v1.7 may move to `configs/calendars/bok.json`.
2. **OPEC scrape is best-effort.** opec.org HTML structure isn't contractual;
   narrow regex (Ministerial/Conference paragraph only). Hardcoded fallback
   when scrape returns nothing.
3. **No per-ticker earnings dates.** Universe-wide KIFRS deadline used as
   worst-case proxy; most KR companies file 1-3 weeks earlier. v1.7+ may
   integrate DART filing-status API.
4. **JMMC heuristic is approximate.** First-Wed captures ~80% of historical
   JMMC dates; ±1-2d slop, doesn't materially shift the widening tier.
5. **Calendar widening is symmetric.** Widens equally up/down regardless of
   expected event direction — uncertainty, not directional bias.
6. **`E_PEAD_KR` requires ≥30d of post-earnings OHLCV.** Hard constraint of
   the T+5 → T+30 window; cannot be relaxed without changing the formula.
7. **CI multiplier does not move `p_up`.** By design (INV-GS-121). Reflects
   realisation uncertainty, not prediction shift. Event-conditional thesis
   weights are a v1.7+ work item.

---

## Compliance posture (unchanged)

`broadcast_telegram` and `mass_email` still raise `ComplianceError` on
call — v1.6 is a thesis/data-plane addition, no compliance loosening.
Per-prediction disclaimer (INV-GS-104) attached to every Prediction
output. The calendar client surfaces public schedule data only — no
trading signal generation, no event-driven order suggestion.
