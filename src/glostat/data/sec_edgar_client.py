from __future__ import annotations

import asyncio
import json
import os
import time
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

import httpx
import structlog

from glostat.core.errors import ConfigError
from glostat.data.retry import RetryStats, with_retry
from glostat.data.sec_edgar_parsers import (
    company_facts_to_payload,
    filings_to_payload,
    holdings_to_payload,
    parse_13f_infotable,
    parse_company_facts,
    parse_submissions_filings,
)
from glostat.data.sec_edgar_types import (
    CompanyFact,
    CompanyFacts,
    Filing,
    FormType,
    HoldingPosition,
    ThirteenFHoldings,
    TickerCikMap,
)
from glostat.data.snapshot_broker import SnapshotBroker, SnapshotKey

# Free-stack SEC EDGAR client. Public, no API key, but User-Agent header MANDATORY
# per https://www.sec.gov/os/accessing-edgar-data — INV-GS-038.
# 10 req/sec rate limit per SEC fair-access policy (kept — SEC's documented cap).
# Sprint 4 PR #3: exponential backoff retry on 429/5xx + Retry-After honor; targets
# the 58% throttle ratio observed in PR #2 by absorbing transient flow control.
# Sprint 1 PR #1: SnapshotBroker integration for ticker_to_cik + get_company_facts.
# Sprint 1 PR #3: get_filings + get_13f_holdings for E_FUND_FLOW.

log: Final = structlog.get_logger(__name__)

_BASE_DATA: Final = "https://data.sec.gov"
_BASE_WWW: Final = "https://www.sec.gov"
_TICKERS_URL: Final = f"{_BASE_WWW}/files/company_tickers.json"

_RATE_LIMIT_PER_SEC: Final[int] = 10
_MIN_INTERVAL_S: Final[float] = 1.0 / _RATE_LIMIT_PER_SEC

_DEFAULT_AGENT: Final = "GLOSTAT research@example.com"
_FORBIDDEN_AGENT_FRAGMENT: Final = "example.com"

_TICKER_CACHE_DEFAULT: Final = Path("cache") / "sec_tickers.json"


class _Throttle:
    def __init__(self, *, rate_per_sec: int = _RATE_LIMIT_PER_SEC) -> None:
        self._sem = asyncio.Semaphore(rate_per_sec)
        self._lock = asyncio.Lock()
        self._next_slot: float = 0.0
        self.acquire_count: int = 0
        self.throttled_count: int = 0

    async def acquire(self) -> None:
        await self._sem.acquire()
        async with self._lock:
            now = time.monotonic()
            wait = max(0.0, self._next_slot - now)
            if wait > 0:
                self.throttled_count += 1
            self._next_slot = max(now, self._next_slot) + _MIN_INTERVAL_S
            self.acquire_count += 1
        if wait > 0:
            await asyncio.sleep(wait)

    def release(self) -> None:
        self._sem.release()


def _resolve_user_agent(*, override: str | None = None) -> str:
    # WHY: SEC requires a contact email; refuse the placeholder so users notice.
    candidate = override or os.environ.get("GLOSTAT_SEC_USER_AGENT") or _DEFAULT_AGENT
    if _FORBIDDEN_AGENT_FRAGMENT in candidate:
        raise ConfigError(
            "INV-GS-038: SEC EDGAR User-Agent must be overridden — "
            "set GLOSTAT_SEC_USER_AGENT='YourApp youraddress@yourdomain'."
        )
    return candidate


class SecEdgarClient:
    def __init__(
        self,
        *,
        user_agent: str | None = None,
        ticker_cache: Path | None = None,
        client: httpx.AsyncClient | None = None,
        snapshot_broker: SnapshotBroker | None = None,
    ) -> None:
        self._user_agent = _resolve_user_agent(override=user_agent)
        self._ticker_cache = ticker_cache or _TICKER_CACHE_DEFAULT
        self._client = client or httpx.AsyncClient(
            headers={"User-Agent": self._user_agent, "Accept-Encoding": "gzip, deflate"},
            timeout=httpx.Timeout(15.0, connect=5.0),
        )
        self._throttle = _Throttle()
        self._tickers: TickerCikMap | None = None
        self._broker = snapshot_broker
        self._last_snapshot_id: str | None = None
        self._retry_stats = RetryStats()

    @property
    def throttle(self) -> _Throttle:
        return self._throttle

    @property
    def retry_stats(self) -> RetryStats:
        return self._retry_stats

    @property
    def user_agent(self) -> str:
        return self._user_agent

    @property
    def last_snapshot_id(self) -> str | None:
        return self._last_snapshot_id

    def attach_snapshot_broker(self, broker: SnapshotBroker) -> None:
        self._broker = broker

    async def aclose(self) -> None:
        await self._client.aclose()

    # ── public surface ─────────────────────────────────────────────────────

    async def ticker_to_cik(self, ticker: str) -> str:
        ticker = ticker.upper().strip()
        if self._tickers is None:
            self._tickers = await self._load_or_fetch_tickers()
        cik = self._tickers.by_ticker.get(ticker)
        if not cik:
            raise KeyError(f"ticker {ticker!r} not found in SEC company_tickers.json")
        self._record_snapshot(
            tool="sec_edgar.company_tickers",
            uaid=f"SEC.TICKER.{ticker}",
            edge_type="ticker_cik",
            ts=self._tickers.fetched_at,
            params={"ticker": ticker},
            payload={"ticker": ticker, "cik": cik},
        )
        return cik

    async def get_filings(
        self,
        cik: str,
        *,
        form_types: Sequence[FormType] = ("10-K", "8-K", "13F"),
        limit: int = 50,
    ) -> tuple[Filing, ...]:
        cik_padded = self._pad_cik(cik)
        url = f"{_BASE_DATA}/submissions/CIK{cik_padded}.json"
        try:
            data = await self._get_json(url)
        except (httpx.HTTPError, ValueError) as exc:
            log.warning("sec_edgar.filings_fetch_failed", cik=cik_padded, err=str(exc))
            return ()
        filings = parse_submissions_filings(
            cik_padded, data, form_types=tuple(form_types), limit=limit
        )
        self._record_snapshot(
            tool="sec_edgar.submissions",
            uaid=f"SEC.CIK{cik_padded}",
            edge_type="filings",
            ts=datetime.now(tz=UTC),
            params={
                "cik": cik_padded,
                "form_types": sorted(str(f) for f in form_types),
                "limit": limit,
            },
            payload=filings_to_payload(filings),
        )
        return tuple(filings)

    async def get_13f_holdings(self, cik: str) -> ThirteenFHoldings | None:
        # WHY: most-recent 13F-HR only; quarterly cadence so caller iterates filings.
        filings = await self.get_filings(cik, form_types=("13F",), limit=1)
        if not filings:
            return None
        return await self.get_13f_for_filing(filings[0])

    async def get_13f_for_filing(self, filing: Filing) -> ThirteenFHoldings | None:
        # WHY: 13F filings ship multiple .xml files; primary doc points to the
        # cover page, but the actual holdings live in a sibling file. Caller may
        # already know the infotable URL — for MVP we fetch the index and pick.
        infotable_url = await self._discover_infotable_url(filing)
        if infotable_url is None:
            return None
        try:
            xml_text = await self._get_text(infotable_url)
        except (httpx.HTTPError, ValueError) as exc:
            log.warning(
                "sec_edgar.infotable_fetch_failed",
                accession=filing.accession_number, err=str(exc),
            )
            return None
        positions = parse_13f_infotable(xml_text)
        holdings = ThirteenFHoldings(
            cik=filing.cik,
            period_of_report=filing.filing_date,
            accession_number=filing.accession_number,
            positions=tuple(positions),
        )
        self._record_snapshot(
            tool="sec_edgar.13f_infotable",
            uaid=f"SEC.13F.{filing.cik}.{filing.accession_number}",
            edge_type="13f_holdings",
            ts=datetime.now(tz=UTC),
            params={
                "cik": filing.cik,
                "accession_number": filing.accession_number,
            },
            payload=holdings_to_payload(positions),
        )
        return holdings

    async def get_company_facts(self, cik: str) -> CompanyFacts:
        cik_padded = self._pad_cik(cik)
        url = f"{_BASE_DATA}/api/xbrl/companyfacts/CIK{cik_padded}.json"
        data = await self._get_json(url)
        company = parse_company_facts(cik_padded, data)
        self._record_snapshot(
            tool="sec_edgar.companyfacts",
            uaid=f"SEC.CIK{cik_padded}",
            edge_type="company_facts",
            ts=datetime.now(tz=UTC),
            params={"cik": cik_padded},
            payload=company_facts_to_payload(company),
        )
        return company

    # ── helpers ────────────────────────────────────────────────────────────

    async def _discover_infotable_url(self, filing: Filing) -> str | None:
        # WHY: 13F primary doc is usually .htm cover; the infotable lives in a
        # sibling XML. We list the filing index JSON and pick *infotable.xml.
        digits = "".join(c for c in filing.cik if c.isdigit())
        acc_no_dashes = filing.accession_number.replace("-", "")
        index_url = (
            f"{_BASE_WWW}/Archives/edgar/data/{int(digits)}/"
            f"{acc_no_dashes}/index.json"
        )
        try:
            data = await self._get_json(index_url)
        except (httpx.HTTPError, ValueError) as exc:
            log.warning("sec_edgar.index_fetch_failed", err=str(exc))
            return None
        items = ((data.get("directory", {}) or {}).get("item", [])) or []
        for item in items:
            name = str(item.get("name", "")).lower()
            if name.endswith("infotable.xml") or name.endswith("info_table.xml"):
                return (
                    f"{_BASE_WWW}/Archives/edgar/data/{int(digits)}/"
                    f"{acc_no_dashes}/{item['name']}"
                )
        return None

    @staticmethod
    def _pad_cik(cik: str) -> str:
        digits = "".join(c for c in str(cik) if c.isdigit())
        return digits.zfill(10)

    def _record_snapshot(
        self,
        *,
        tool: str,
        uaid: str,
        edge_type: str,
        ts: datetime,
        params: dict[str, Any],
        payload: dict[str, Any],
    ) -> None:
        if self._broker is None:
            self._last_snapshot_id = None
            return
        params_canon = json.dumps(params, sort_keys=True, separators=(",", ":"))
        key = SnapshotKey(
            uaid=uaid,
            edge_type=edge_type,
            ts_utc=ts,
            tool=tool,
            params_canon=params_canon,
        )
        rec = self._broker.save_snapshot(key, payload)
        self._last_snapshot_id = rec.leaf.leaf_hash

    async def _get_json(self, url: str) -> dict[str, Any]:
        async def _fetch() -> dict[str, Any]:
            await self._throttle.acquire()
            try:
                r = await self._client.get(url)
                r.raise_for_status()
                return r.json()
            finally:
                self._throttle.release()

        return await with_retry(
            _fetch,
            stats=self._retry_stats,
            operation=f"sec_edgar.json:{url}",
        )

    async def _get_text(self, url: str) -> str:
        async def _fetch() -> str:
            await self._throttle.acquire()
            try:
                r = await self._client.get(url)
                r.raise_for_status()
                return r.text
            finally:
                self._throttle.release()

        return await with_retry(
            _fetch,
            stats=self._retry_stats,
            operation=f"sec_edgar.text:{url}",
        )

    async def _load_or_fetch_tickers(self) -> TickerCikMap:
        if self._ticker_cache.exists():
            try:
                payload = json.loads(self._ticker_cache.read_text())
                return TickerCikMap(by_ticker=dict(payload.get("by_ticker", {})))
            except (OSError, json.JSONDecodeError):
                pass

        async def _fetch_raw() -> Any:
            await self._throttle.acquire()
            try:
                r = await self._client.get(_TICKERS_URL)
                r.raise_for_status()
                return r.json()
            finally:
                self._throttle.release()

        raw = await with_retry(
            _fetch_raw,
            stats=self._retry_stats,
            operation="sec_edgar.company_tickers",
        )
        by_ticker: dict[str, str] = {}
        # company_tickers.json shape: {"0": {"cik_str": int, "ticker": str, "title": str}, ...}
        for entry in raw.values() if isinstance(raw, dict) else []:
            ticker = str(entry.get("ticker", "")).upper().strip()
            cik_int = entry.get("cik_str")
            if ticker and cik_int is not None:
                by_ticker[ticker] = str(cik_int).zfill(10)
        mapping = TickerCikMap(by_ticker=by_ticker)
        try:
            self._ticker_cache.parent.mkdir(parents=True, exist_ok=True)
            self._ticker_cache.write_text(json.dumps({"by_ticker": by_ticker}))
        except OSError as exc:
            log.warning("sec_tickers.cache_write_failed", err=str(exc))
        return mapping


__all__ = [
    "CompanyFact",
    "CompanyFacts",
    "Filing",
    "FormType",
    "HoldingPosition",
    "SecEdgarClient",
    "ThirteenFHoldings",
    "TickerCikMap",
]
