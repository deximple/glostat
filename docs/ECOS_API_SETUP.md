# ECOS API Setup (v1.3 M2)

> Status: ACTIVE 2026-04-29. KR macro overlay (BoK base rate, KRW/USD, CPI,
> KOSPI index) for the `E_MACRO_KR` expert.
>
> Information tool. Not investment advice. ECOS is the official Bank of Korea
> Economic Statistics System OpenAPI — public macro time series.

---

## What this unlocks

When `GLOSTAT_ECOS_API_KEY` is set, GLOSTAT enables the `E_MACRO_KR` thesis:

- **BoK base rate** (722Y001 / 0101000) — 3-month change drives the
  rate-direction term (cuts → equity bullish).
- **KRW/USD daily exchange rate** (731Y001 / 0000001) — 60-day trend feeds
  the exporter-conviction term (KRW weakening → exporters bullish).
- **Consumer Price Index** (901Y009 / 0) — surprise vs trailing 12-month mean
  feeds the tightening-fear term (above-trend CPI → equity bearish).
- **KOSPI index** (802Y001 / 0001000) — 60-day momentum feeds the
  continuation term.
- **FX reserves** (732Y001) — currently logged for context, not yet aggregated.

Without ECOS configured, `E_MACRO_KR` reports a clean universe-aware skip with
a pointer back here. KR predictions still run; they just lose the macro slot.

---

## Registration (free, ~2 minutes)

1. Visit
   https://ecos.bok.or.kr/jsp/openapi/OpenApiController.jsp?t=mainPage
   (in Korean).
2. Click `OpenAPI 인증키 신청` and complete the application form. Use a real
   email — ECOS emails the API key to that address.
3. Wait ~1 minute for the email; the key is a 20-char alphanumeric string.

ECOS free tier: **10,000 API calls per day per registered key**. Far more than
GLOSTAT's typical workload (one KR prediction with ECOS = 4 calls: base rate
+ KRW/USD + CPI + KOSPI; cached intra-day in the Snapshot Broker).

---

## Configuration

```bash
export GLOSTAT_ECOS_API_KEY="<your-20-char-key>"
```

Or persistently:

```bash
echo 'export GLOSTAT_ECOS_API_KEY="<your-key>"' >> ~/.zshrc
```

For one-off invocation:

```bash
GLOSTAT_ECOS_API_KEY="<your-key>" \
GLOSTAT_SEC_USER_AGENT="Your Name your@email" \
  uv run glostat predict 005930
```

---

## Verifying setup

```bash
GLOSTAT_ECOS_API_KEY="<your-key>" uv run python -c "
import asyncio
from datetime import date
from glostat.data.ecos_client import EcosClient, is_ecos_configured

print('configured:', is_ecos_configured())

async def main():
    client = EcosClient()
    s = await client.get_base_rate(date(2026, 1, 1), date(2026, 3, 31))
    print('base_rate n_obs:', len(s.observations))
    for o in s.observations:
        print(' ', o.period, '→', o.value, o.unit)
    await client.aclose()

asyncio.run(main())
"
```

Expected output (sample):

```
configured: True
base_rate n_obs: 3
  202601 → 2.5 연%
  202602 → 2.5 연%
  202603 → 2.5 연%
```

---

## Endpoints used

| Statistic | Code | Item | Cycle | Source |
|-----------|------|------|-------|--------|
| 한국은행 기준금리 | 722Y001 | 0101000 | M | BoK monetary policy |
| 원/달러 환율 (매매기준율) | 731Y001 | 0000001 | D | BoK FX |
| 소비자물가지수 (총지수) | 901Y009 | 0 | M | KOSIS via BoK |
| 외환보유액 | 732Y001 | 99 | M | BoK FX reserves |
| KOSPI 지수 | 802Y001 | 0001000 | D | KRX via BoK |

All API responses persist to the Snapshot Broker (Merkle leaf hash) so
`glostat replay <hash>` reproduces deterministic predictions.

---

## Rate limits

- ECOS policy: 10 req/sec per key (we self-throttle to match)
- Daily cap: 10,000 calls
- KR-typical workload: predict 1 KR ticker with macro = 4 ECOS calls;
  hindcast 30 KR tickers × 60-day stride at most ~20 ECOS calls per stride
  (macro series cached; same series used across all tickers in the stride).

If you exceed the daily cap, ECOS returns `RESULT.CODE` like `INFO-100` /
`ERROR-300`; we raise `EcosApiError`. Retry the next day.

---

## Privacy + ToS

- ECOS API serves **public** macro statistics; no PII is exchanged
- The API key is **personal** — do not commit it to source control
- ECOS ToS: https://ecos.bok.or.kr/api/  (read before use)
- Snapshot Broker stores full payload locally; never sync `cache/snapshots/`
  to public repos

---

## Troubleshooting

**`EcosApiKeyMissingError`** — env var unset; export `GLOSTAT_ECOS_API_KEY`.

**`EcosApiError: code=INFO-100`** — invalid key. Check the email and copy
the alphanumeric key without surrounding whitespace.

**`EcosApiError: code=INFO-200`** — handled silently by the client (returns
empty series). If macro-only signals are missing, your requested window may
predate the series start.

**`E_MACRO_KR` always returns NEUTRAL** — KR macro shifts slowly; it's
expected to land NEUTRAL most weeks. The signal fires around BoK rate
changes, abrupt KRW moves, or CPI prints.

---

## Future work

- Sector-aware export-exposure tilt (megacap exporters vs domestic-focused).
- KOSDAQ-specific macro adjustments (smaller / domestic mix).
- Add KR Treasury yield curve series for term-spread input.
