from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import UTC, date, datetime
from typing import Any

from glostat.data.sec_edgar_client import CompanyFact, CompanyFacts
from glostat.data.sec_edgar_types import Filing, HoldingPosition, ThirteenFHoldings
from glostat.data.snapshot_broker import SnapshotBroker, SnapshotKey
from glostat.data.yfinance_types import (
    DividendEvent,
    DividendHistory,
    EarningsCalendar,
    EarningsEvent,
    Fundamentals,
    HoldersKind,
    HoldersSnapshot,
    OhlcvBar,
    OhlcvSeries,
)

# Mock data clients used by `glostat predict --mock` and the CLI tests.
# Reproduce the public surface of YFinanceClient / SecEdgarClient consumed by
# all wired Experts and feed snapshots into the broker so the rest of the
# pipeline behaves identically to live mode.

_FIXED_TS: datetime = datetime(2026, 4, 28, 12, 0, tzinfo=UTC)


class MockYFinanceClient:
    def __init__(self, *, broker: SnapshotBroker, fixture: Mapping[str, Any]) -> None:
        self._broker = broker
        self._fixture = fixture
        self.last_snapshot_id: str | None = None

    async def get_fundamentals(self, ticker: str) -> Fundamentals:
        body = self._fixture["fundamentals"]
        f = Fundamentals(
            ticker=str(body["ticker"]),
            pe_ratio=body.get("pe_ratio"),
            forward_pe=body.get("forward_pe"),
            eps=body.get("eps"),
            forward_eps=body.get("forward_eps"),
            roe=body.get("roe"),
            market_cap=body.get("market_cap"),
            dividend_yield=body.get("dividend_yield"),
            beta=body.get("beta"),
            fifty_two_week_high=body.get("fifty_two_week_high"),
            fifty_two_week_low=body.get("fifty_two_week_low"),
            raw=(),
        )
        self._save("fundamentals", "yfinance.info.mock", ticker, body)
        return f

    async def get_ohlcv(
        self,
        ticker: str,
        *,
        start: date,
        end: date,
        interval: str = "1d",
    ) -> OhlcvSeries:
        raw_bars = self._fixture.get("ohlcv", [])
        bars = tuple(
            OhlcvBar(
                ts=_parse_ts(b["ts"]),
                open=float(b["open"]),
                high=float(b["high"]),
                low=float(b["low"]),
                close=float(b["close"]),
                volume=int(b["volume"]),
                adj_close=float(b.get("adj_close", b["close"])),
            )
            for b in raw_bars
        )
        series = OhlcvSeries(ticker=ticker.upper(), bars=bars, interval=interval)
        self._save(
            "ohlcv",
            "yfinance.history.mock",
            ticker,
            {"ticker": ticker.upper(), "bars": list(raw_bars), "interval": interval},
            extra_params={"start": start.isoformat(), "end": end.isoformat()},
        )
        return series

    async def get_earnings_calendar(self, ticker: str) -> EarningsCalendar:
        body = self._fixture.get(
            "earnings_calendar",
            {"ticker": ticker.upper(), "upcoming": []},
        )
        events = tuple(
            EarningsEvent(
                ticker=str(e.get("ticker", ticker.upper())),
                earnings_date=_parse_ts(e["earnings_date"]),
                eps_estimate=e.get("eps_estimate"),
                eps_actual=e.get("eps_actual"),
                revenue_estimate=e.get("revenue_estimate"),
            )
            for e in body.get("upcoming", [])
        )
        cal = EarningsCalendar(ticker=ticker.upper(), upcoming=events)
        self._save("earnings_calendar", "yfinance.calendar.mock", ticker, body)
        return cal

    async def get_dividends(self, ticker: str) -> DividendHistory:
        body = self._fixture.get("dividends", {"ticker": ticker.upper(), "events": []})
        events = tuple(
            DividendEvent(
                ex_date=date.fromisoformat(e["ex_date"]),
                amount=float(e["amount"]),
            )
            for e in body.get("events", [])
        )
        h = DividendHistory(ticker=ticker.upper(), events=events)
        self._save("dividends", "yfinance.dividends.mock", ticker, body)
        return h

    async def get_holders(
        self, ticker: str, *, kind: HoldersKind = "institutional"
    ) -> HoldersSnapshot:
        body = self._fixture.get(
            "institutional_holders",
            {"ticker": ticker.upper(), "holders": []},
        )
        holders = tuple(
            (str(h["name"]), float(h.get("pct_held", 0.0)))
            for h in body.get("holders", [])
        )
        rows = tuple(
            (
                str(h["name"]),
                float(h.get("pct_held", 0.0)),
                int(h.get("shares", 0) or 0),
                str(h.get("date_reported", "") or ""),
            )
            for h in body.get("holders", [])
        )
        snap = HoldersSnapshot(
            ticker=ticker.upper(), kind=kind, holders=holders, fetched_at=_FIXED_TS,
            rows=rows,
        )
        self._save(
            f"holders.{kind}", "yfinance.holders.mock", ticker, body,
            extra_params={"kind": kind},
        )
        return snap

    def _save(
        self,
        edge_type: str,
        tool: str,
        ticker: str,
        payload: Mapping[str, Any],
        *,
        extra_params: Mapping[str, str] | None = None,
    ) -> None:
        params: dict[str, Any] = {"ticker": ticker.upper(), "mock": True}
        if extra_params:
            params.update(extra_params)
        key = SnapshotKey(
            uaid=f"XNAS.{ticker.upper()}",
            edge_type=edge_type,
            ts_utc=_FIXED_TS,
            tool=tool,
            params_canon=json.dumps(params, sort_keys=True, separators=(",", ":")),
        )
        rec = self._broker.save_snapshot(key, dict(payload))
        self.last_snapshot_id = rec.leaf.leaf_hash


class MockSecEdgarClient:
    def __init__(self, *, broker: SnapshotBroker, fixture: Mapping[str, Any]) -> None:
        self._broker = broker
        self._fixture = fixture
        self.last_snapshot_id: str | None = None

    async def ticker_to_cik(self, ticker: str) -> str:
        cik = str(self._fixture["company_facts"]["cik"])
        key = SnapshotKey(
            uaid=f"SEC.TICKER.{ticker.upper()}",
            edge_type="ticker_cik",
            ts_utc=_FIXED_TS,
            tool="sec_edgar.company_tickers.mock",
            params_canon=json.dumps(
                {"ticker": ticker.upper(), "mock": True},
                sort_keys=True, separators=(",", ":"),
            ),
        )
        rec = self._broker.save_snapshot(key, {"ticker": ticker.upper(), "cik": cik})
        self.last_snapshot_id = rec.leaf.leaf_hash
        return cik

    async def get_filings(
        self,
        cik: str,
        *,
        form_types: tuple[str, ...] = ("10-K", "8-K", "13F"),
        limit: int = 50,
    ) -> tuple[Filing, ...]:
        body = self._fixture.get("13f_filings", [])
        wanted = {f.upper() for f in form_types}
        out: list[Filing] = []
        for f in body:
            form = str(f.get("form_type", "")).upper()
            if not (form in wanted or any(form.startswith(w) for w in wanted)):
                continue
            out.append(
                Filing(
                    cik=str(f.get("cik", cik)),
                    accession_number=str(f["accession_number"]),
                    form_type=form,
                    filing_date=date.fromisoformat(str(f["filing_date"])),
                    primary_document=str(f.get("primary_document", "")),
                    primary_doc_url=str(f.get("primary_doc_url", "")),
                )
            )
            if len(out) >= limit:
                break
        key = SnapshotKey(
            uaid=f"SEC.CIK{cik}",
            edge_type="filings",
            ts_utc=_FIXED_TS,
            tool="sec_edgar.submissions.mock",
            params_canon=json.dumps(
                {"cik": cik, "form_types": sorted(wanted), "limit": limit, "mock": True},
                sort_keys=True, separators=(",", ":"),
            ),
        )
        rec = self._broker.save_snapshot(
            key, {"count": len(out), "filings": [_filing_to_dict(f) for f in out]}
        )
        self.last_snapshot_id = rec.leaf.leaf_hash
        return tuple(out)

    async def get_13f_for_filing(self, filing: Filing) -> ThirteenFHoldings | None:
        holdings_body = self._fixture.get("13f_holdings", {})
        positions_raw = holdings_body.get(filing.accession_number, [])
        if not positions_raw:
            return None
        positions = tuple(
            HoldingPosition(
                cusip=str(p["cusip"]),
                name=str(p.get("name", "")),
                shares=int(p["shares"]),
                market_value_usd=float(p.get("market_value_usd", 0.0)),
                put_call=p.get("put_call"),
            )
            for p in positions_raw
        )
        holdings = ThirteenFHoldings(
            cik=filing.cik,
            period_of_report=filing.filing_date,
            accession_number=filing.accession_number,
            positions=positions,
        )
        key = SnapshotKey(
            uaid=f"SEC.13F.{filing.cik}.{filing.accession_number}",
            edge_type="13f_holdings",
            ts_utc=_FIXED_TS,
            tool="sec_edgar.13f_infotable.mock",
            params_canon=json.dumps(
                {
                    "cik": filing.cik,
                    "accession_number": filing.accession_number,
                    "mock": True,
                },
                sort_keys=True, separators=(",", ":"),
            ),
        )
        rec = self._broker.save_snapshot(
            key, {"count": len(positions), "positions": list(positions_raw)}
        )
        self.last_snapshot_id = rec.leaf.leaf_hash
        return holdings

    async def get_13f_holdings(self, cik: str) -> ThirteenFHoldings | None:
        filings = await self.get_filings(cik, form_types=("13F",), limit=1)
        if not filings:
            return None
        return await self.get_13f_for_filing(filings[0])

    async def get_company_facts(self, cik: str) -> CompanyFacts:
        body = self._fixture["company_facts"]
        facts = tuple(
            CompanyFact(
                concept=str(f["concept"]),
                unit=str(f["unit"]),
                value=float(f["value"]),
                period_end=datetime.fromisoformat(f["period_end"]).date(),
                fiscal_year=int(f["fiscal_year"]),
                fiscal_period=str(f.get("fiscal_period", "")),
                form=str(f.get("form", "")),
            )
            for f in body.get("facts", [])
        )
        company = CompanyFacts(
            cik=str(body["cik"]),
            entity_name=str(body.get("entity_name", "")),
            facts=facts,
        )
        key = SnapshotKey(
            uaid=f"SEC.CIK{cik}",
            edge_type="company_facts",
            ts_utc=_FIXED_TS,
            tool="sec_edgar.companyfacts.mock",
            params_canon=json.dumps(
                {"cik": cik, "mock": True},
                sort_keys=True, separators=(",", ":"),
            ),
        )
        rec = self._broker.save_snapshot(key, body)
        self.last_snapshot_id = rec.leaf.leaf_hash
        return company


def _filing_to_dict(f: Filing) -> dict[str, Any]:
    return {
        "cik": f.cik,
        "accession_number": f.accession_number,
        "form_type": f.form_type,
        "filing_date": f.filing_date.isoformat(),
        "primary_document": f.primary_document,
        "primary_doc_url": f.primary_doc_url,
    }


def _parse_ts(s: str) -> datetime:
    # WHY: fixture stores either bare date "2025-08-29" (OHLCV) or full ISO datetime.
    if "T" in s:
        return datetime.fromisoformat(s)
    return datetime.fromisoformat(s + "T00:00:00+00:00")


__all__ = ["MockSecEdgarClient", "MockYFinanceClient"]
