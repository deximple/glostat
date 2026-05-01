# GLOSTAT — KIS Open API Setup (v1.4 N1)

> Status: ACTIVE 2026-04-29. Required for v1.4 N1 KR 3-source investor flows
> + E_INTRADAY_FLOW_KR. Without these credentials the predictor falls back to
> Naver-only intraday flows; everything still works, but real-time intraday
> KIS coverage is unavailable.
>
> Information tool. Read-only paths only. Order-execution endpoints are
> intentionally NOT wrapped (INV-GS-101 forbids action output).

---

## What this enables

KIS Open API exposes:

- Real-time intraday investor flows (외국인 / 기관 / 개인 / 프로그램 net buy)
- End-of-day daily summary in 원화 (KRW)
- Per-ticker quote + volume snapshots (not used by GLOSTAT today)

GLOSTAT v1.4 N1 wires the first two as part of `E_FOREIGN_REVERSAL` 3-source
fusion + `E_INTRADAY_FLOW_KR` overlay. Coverage gap when missing: today's
intraday foreign net is unknown until end-of-day Naver scrape, and the
3-source disagreement guard cannot cross-check Toss.

---

## Step 1 — Register for an account

1. Visit https://apiportal.koreainvestment.com/
2. Create a Korea Investment & Securities (한국투자증권) account if you
   don't already have one. Brokerage account number not required for the
   API portal itself, but is required for paper trading (which GLOSTAT
   does not use).
3. Verify identity with PASS / 공동인증서 / 휴대폰.

## Step 2 — Issue an app key + secret

1. Sign in to the API portal.
2. Navigate to "앱 키 발급" (issue app key).
3. Choose either **모의투자** (paper, free) or **실전** (live) — GLOSTAT
   only consumes read-only endpoints, so paper is fine for development.
4. Save the `app_key` and `app_secret` issued to you.

## Step 3 — Export environment variables

```bash
export GLOSTAT_KIS_APP_KEY="<your-app-key>"
export GLOSTAT_KIS_APP_SECRET="<your-app-secret>"
```

Add these to your shell rc file (`~/.zshrc` / `~/.bashrc`) for persistence.

To use the paper environment instead of live, pass `paper=True` when
constructing `KisClient` in tests / scripts. The CLI defaults to live.

## Step 4 — Verify

```bash
uv run python -c "
from glostat.data.kis_client import is_kis_configured
print('KIS configured:', is_kis_configured())
"
```

Expected: `KIS configured: True`.

Run the live KIS smoke test:

```bash
NETWORK_TESTS=1 uv run pytest -q tests/test_kis_client.py
```

Run a live prediction (KIS optional — falls back to Naver if absent):

```bash
GLOSTAT_SEC_USER_AGENT="Your Name your@email" \
GLOSTAT_KIS_APP_KEY="..." GLOSTAT_KIS_APP_SECRET="..." \
uv run glostat predict 005930
```

Check the contributing signals — `E_INTRADAY_FLOW_KR` and
`E_FOREIGN_REVERSAL` should now show `data_sources` containing `kis`.

---

## Rate limits

KIS publishes 20 req/sec for the standard tier. The client self-throttles
to that cap and refreshes the OAuth token automatically (10-minute margin
before the 1-day expiry). Concurrent CPU-bound consumers are fine.

---

## What we do NOT do

GLOSTAT is a **prediction tool**. The KIS client deliberately does NOT wrap:

- POST `/uapi/.../inquire-balance/order` (cash buy / sell)
- POST `/uapi/.../inquire-balance/order/cancel`
- Any websocket endpoint (real-time price stream)

INV-GS-101 (no BUY/SELL action output) and INV-GS-024 (no broadcast) make
order placement and broadcast-style outputs out of scope. If you need a KIS
order client, see MOET (`/Applications/MOET/src/moet/core/paper_kis.py`)
which is a separate paper-trading harness with its own compliance posture.

---

## Troubleshooting

**`KisCredentialsMissingError`** — env vars not exported in the shell that
launched GLOSTAT. Re-export and retry, or pass `app_key=` / `app_secret=`
to the constructor directly in scripts.

**`KisApiError: rt_cd=1 msg=한도 초과`** — daily limit hit. Wait a few
minutes; the throttle should keep you below the cap in normal use.

**`KisApiError: KIS token request failed`** — wrong app_key/secret, or
KIS server outage. Re-check credentials and try again. KIS occasionally
takes the OAuth endpoint offline for maintenance late at night KST.

---

## Compliance posture (unchanged)

Every Prediction emitted with KIS data still carries the personal-use
disclaimer (INV-GS-024 + INV-GS-104). KIS data is used only to inform the
probability output — never to execute orders or broadcast signals.
