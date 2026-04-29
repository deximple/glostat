# DART API Setup (v1.2 L2)

> Status: ACTIVE 2026-04-29. KR fundamentals + 임원 거래 enrichment.
>
> Information tool. Not investment advice. DART is the Korean equivalent of
> SEC EDGAR — official electronic filings + corp metadata.

---

## What this unlocks

When `GLOSTAT_DART_API_KEY` is set, GLOSTAT enables two extra capabilities:

1. **`E_INSIDER_KR`** — KR insider cluster expert (DART `elestock.json`),
   the Korean equivalent of US `E_INSIDER_CLUSTER` (Form 4). Cluster threshold
   3+ executive buys within 14 days → LONG signal.
2. **DART overlay on `E_FUNDAMENTAL_KR`** — overrides yfinance ROE / EPS with
   DART XBRL filing values when the latest annual report is available.
   yfinance KR ROE is partial; DART is canonical.

Without DART configured, both behave gracefully: `E_INSIDER_KR` reports
`skip (DART API not configured ...)` and `E_FUNDAMENTAL_KR` falls back to
yfinance-only fields.

---

## Registration (free, ~2 minutes)

1. Visit https://opendart.fss.or.kr/ (in Korean).
2. Click `오픈API → 인증키 신청` (top nav).
3. Fill in the application form. Use a real email — DART will send the API
   key to that address.
4. Wait ~1 minute for the email; the API key is a 40-char hex string.

DART's free tier: **10,000 API calls per day per registered key**. Far more
than GLOSTAT's typical predict / hindcast workload (a single SK이노베이션
prediction with DART overlay = 3 calls: `corpCode.xml` (cached after 1st run)
+ `company.json` + `fnlttSinglAcntAll.json`).

---

## Configuration

Export the key before running `glostat predict` / `glostat kr-hindcast`:

```bash
export GLOSTAT_DART_API_KEY="<your-40-char-hex-key>"
```

Or add to your shell profile (`~/.zshrc` / `~/.bashrc`):

```bash
echo 'export GLOSTAT_DART_API_KEY="<your-key>"' >> ~/.zshrc
```

For one-off invocation:

```bash
GLOSTAT_DART_API_KEY="<your-key>" \
GLOSTAT_SEC_USER_AGENT="Your Name your@email" \
  uv run glostat predict 096770
```

---

## Verifying setup

Smoke test:

```bash
uv run python -c "
from glostat.data.dart_client import is_dart_configured, DartClient
print('configured:', is_dart_configured())
import asyncio
async def main():
    client = DartClient()
    code = await client.get_corp_code('005930')  # 삼성전자
    print('Samsung corp_code:', code)
    await client.aclose()
asyncio.run(main())
"
```

Expected output (sample):
```
configured: True
Samsung corp_code: 00126380
```

---

## Endpoints used

| Endpoint | Purpose | Cache |
|----------|---------|-------|
| `/api/corpCode.xml` | KRX 6-digit → DART 8-digit corp_code map | `cache/dart/corp_code.parquet` (cold once) |
| `/api/company.json` | Company overview (sector, market, CEO) | Snapshot Broker |
| `/api/fnlttSinglAcntAll.json` | Quarterly / annual financial statements | Snapshot Broker |
| `/api/elestock.json` | Executive stock transactions (180d window) | Snapshot Broker |

All API responses persist to the Snapshot Broker (Merkle leaf hash) so
`glostat replay <hash>` reproduces deterministic predictions.

---

## Rate limits

- DART policy: 10 req/sec (we self-throttle to match)
- Daily cap: 10,000 calls
- KR-typical workload: predict 1 KR ticker = 3 DART calls; hindcast 30
  KR tickers × 60-day stride = ~360 calls (well under cap)

If you exceed the daily cap, DART returns `status=020` and `DartApiError` is
raised. Retry the next day.

---

## Privacy + ToS

- DART API is **public** government data; no PII is exchanged
- The API key is **personal** — do not commit it to source control
- DART ToS: https://opendart.fss.or.kr/intro/main.do — read before use
- We send no user identity besides the registered email tied to the key
- Snapshot Broker stores full payload → cache it locally; never sync the
  `cache/dart/` directory to public repos

---

## Troubleshooting

**`DartApiKeyMissingError`** — env var unset; export `GLOSTAT_DART_API_KEY`.

**`DartApiError: status=010`** — invalid key. Re-check the email; copy the
40-char hex without surrounding whitespace.

**`DartApiError: status=020`** — daily cap exceeded. Wait until 00:00 KST.

**`DartApiError: no DART corp_code for KRX ...`** — ticker is unlisted /
delisted / KOSDAQ junior board. Most KOSPI 200 names resolve cleanly.

**`E_INSIDER_KR` always returns NEUTRAL** — large-cap names rarely cluster
3+ executive buys; this is expected. The signal fires for mid-caps and
post-correction insider buying patterns.
