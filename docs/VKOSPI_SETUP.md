# VKOSPI Data Setup

GLOSTAT's `E_VKOSPI_MOOD_KR` thesis (INV-GS-134, Lee/Son/Lee 2024) requires
daily VKOSPI close prices to compute event-day ΔVKOSPI. Live programmatic
fetch is intentionally not bundled — both KRX and Naver paths have material
issues. This doc explains why and how to plug in your own backend.

---

## The data-source landscape (2026-05)

### Option A — KRX Information Data System (recommended for archival)

URL: <https://data.krx.co.kr/>

Index path: 통계 → 기본 통계 → 지수 → 주가지수 → 일별시세 → 변동성지수 (V-KOSPI)

**Programmatic access blocked**: the AJAX endpoint
`/comm/bldAttendant/getJsonData.cmd` returns `LOGOUT` (HTTP 400) without a
stateful session-cookie + menuId trail set by visiting the chart page first.
Reverse-engineering the auth without a headless browser is fragile and breaks
when KRX rotates their session key.

**Recommended workflow**:
1. Open the URL above and select **변동성지수 (V-KOSPI)**
2. Set the date range (e.g. 2024-01-01..2026-12-31)
3. Click **CSV 다운로드** (top-right button)
4. Save to `cache/vkospi_history.csv`

A quarterly manual export is sufficient for the GLOSTAT calibration window —
the hindcast harness loads the full file once.

### Option B — Naver Finance (no longer works)

The historical day-series page <https://finance.naver.com/sise/sise_index_day.naver?code=VKOSPI>
returns 200 OK but with an empty data table. Naver removed VKOSPI from this
endpoint sometime before 2026. The world-index endpoint
(`/world/sise.naver?symbol=VKOSPI`) returns 346 bytes regardless of the
symbol.

If Naver restores VKOSPI here in the future, write a Naver provider mirroring
the existing `naver_kr_client` HTML scrape pattern.

### Option C — paid data feeds

Bloomberg, Refinitiv, FnGuide, Quantiwise, etc. all carry VKOSPI. Out of
scope for the free-stack GLOSTAT default; INV-GS-036 gates paid sources
behind explicit Phase 2+ consent.

---

## Plugging in the CSV backend

```python
from pathlib import Path

from glostat.data.vkospi_client import VkospiClient
from glostat.data.vkospi_csv_provider import attach_csv_provider

client = VkospiClient(snapshot_broker=broker)  # broker optional
attach_csv_provider(client, Path("cache/vkospi_history.csv"))

bars = await client.get_history(
    start=date(2024, 1, 1), end=date(2026, 3, 31),
)
delta = await client.get_delta_at(date(2026, 4, 30))
```

### CSV format

Minimum two columns; header row optional, either column order accepted.

```csv
date,close
2024-01-02,18.42
2024-01-03,17.95
2024-01-04,18.01
```

Accepted date formats:
- `YYYY-MM-DD` (ISO 8601)
- `YYYY/MM/DD`
- `YYYY.MM.DD` (Naver-style)
- `YYYYMMDD` (KRX-export-style)

Accepted header tokens (case-insensitive):
- date column: `date`, `trd_dd`, `일자`, `날짜`, `기준일`
- close column: `close`, `closing`, `체결가`, `종가`, `현재가`, `value`

Lines starting with `#` are treated as comments and skipped. Blank lines
are skipped. Duplicate dates in the same file are deduplicated, with the
LAST occurrence winning (useful when concatenating quarterly KRX exports).

---

## Refresh cadence

The Lee/Son/Lee 2024 calibration window ends 2022-07. For OOS validation
GLOSTAT needs at least 2023-2026 of VKOSPI history. Refresh the CSV
quarterly (or monthly during active calibration cycles) — the cost is
~30 seconds of operator time per export.

---

## Verification

After exporting:

```bash
uv run python -c "
from datetime import date
from pathlib import Path
from glostat.data.vkospi_csv_provider import parse_csv
bars = parse_csv(Path('cache/vkospi_history.csv'))
print(f'n_bars={len(bars)}')
print(f'first={bars[0].bar_date} close={bars[0].close}')
print(f'last={bars[-1].bar_date} close={bars[-1].close}')
"
```

Expected output for a healthy 2024-01-01..2026-03-31 export:
- `n_bars` ≈ 550 (roughly 252 trading days/year × 2 years + holidays)
- `first` close in the 12-25 range (KOSPI 200 implied vol typical band)
- `last` close in the 12-25 range

---

## Caveats inherited from the underlying paper + INV-GS-134

The 12 caveats embedded in `experts/e_vkospi_mood_kr.py` apply regardless
of data source. In particular:

- **Caveat 9**: VKOSPI ↔ KOSPI200 cross-correlation is concentrated at
  lag 0 only (KRX 2009 Table 15). Predict-time entry must be next-trading-
  day-open at the earliest.
- **Caveat 10**: KRX 2009 study has institutional bias (KRX-published);
  treat the "VKOSPI > historical-vol for realized-vol prediction" claim
  as a weak prior, not validation.
- **Caveat 12**: Calibration > prediction. The expert is a filter for
  "when not to bet" via `|r|>10%` AND aligned-ΔVKOSPI gate, not a broad
  next-day predictor.
