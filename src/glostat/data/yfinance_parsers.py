from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

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

# Yahoo response parsers extracted from yfinance_client to keep that file ≤400 lines.
# Pure functions only — sync workers run inside asyncio.to_thread from the client.


def as_float(v: Any) -> float | None:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def as_dt(v: Any) -> datetime:
    if isinstance(v, datetime):
        return v
    if isinstance(v, list) and v:
        return as_dt(v[0])
    if hasattr(v, "to_pydatetime"):
        return v.to_pydatetime()
    if isinstance(v, str):
        return datetime.fromisoformat(v)
    return datetime.now(tz=UTC)


def coerce_date(v: Any) -> date:
    if isinstance(v, date) and not isinstance(v, datetime):
        return v
    if isinstance(v, datetime):
        return v.date()
    if hasattr(v, "to_pydatetime"):
        return v.to_pydatetime().date()
    if isinstance(v, str):
        return date.fromisoformat(v[:10])
    raise ValueError(f"cannot coerce {v!r} to date")


def parse_ohlcv(yf: Any, ticker: str, start: date, end: date, interval: str) -> list[OhlcvBar]:
    ticker_obj = yf.Ticker(ticker.upper())
    df = ticker_obj.history(start=start.isoformat(), end=end.isoformat(), interval=interval)
    if df is None:
        return []
    out: list[OhlcvBar] = []
    try:
        iterator = df.iterrows()
    except (AttributeError, TypeError):
        return []
    for ts, row in iterator:
        try:
            pyts = ts.to_pydatetime() if hasattr(ts, "to_pydatetime") else ts
            if isinstance(pyts, str):
                pyts = datetime.fromisoformat(pyts)
            out.append(
                OhlcvBar(
                    ts=pyts,
                    open=float(row["Open"]),
                    high=float(row["High"]),
                    low=float(row["Low"]),
                    close=float(row["Close"]),
                    volume=int(row["Volume"]),
                    adj_close=float(row["Close"]),
                )
            )
        except (KeyError, TypeError, ValueError):
            # WHY: skip rows missing required fields rather than crash whole fetch.
            continue
    return out


def parse_fundamentals(yf: Any, ticker: str) -> Fundamentals:
    info = yf.Ticker(ticker.upper()).info or {}
    return Fundamentals(
        ticker=ticker.upper(),
        pe_ratio=as_float(info.get("trailingPE")),
        forward_pe=as_float(info.get("forwardPE")),
        eps=as_float(info.get("trailingEps")),
        forward_eps=as_float(info.get("forwardEps")),
        roe=as_float(info.get("returnOnEquity")),
        market_cap=as_float(info.get("marketCap")),
        dividend_yield=as_float(info.get("dividendYield")),
        beta=as_float(info.get("beta")),
        fifty_two_week_high=as_float(info.get("fiftyTwoWeekHigh")),
        fifty_two_week_low=as_float(info.get("fiftyTwoWeekLow")),
        raw=tuple(sorted((k, v) for k, v in info.items() if isinstance(v, (int, float, str)))),
    )


def parse_dividends(yf: Any, ticker: str) -> list[DividendEvent]:
    ticker_obj = yf.Ticker(ticker.upper())
    try:
        series = ticker_obj.dividends
    except (AttributeError, TypeError, ValueError):
        return []
    if series is None:
        return []
    try:
        iterator = series.items()
    except AttributeError:
        return []
    out: list[DividendEvent] = []
    for ts, amount in iterator:
        try:
            ex_date = ts.date() if hasattr(ts, "date") else coerce_date(ts)
            out.append(DividendEvent(ex_date=ex_date, amount=float(amount)))
        except (TypeError, ValueError):
            continue
    return out


def parse_recommendations(yf: Any, ticker: str) -> list[Any]:
    # v1.8.0 — sell-side analyst rec changes via yfinance.Ticker.upgrades_downgrades.
    # Returns list of AnalystRecommendationEvent. Tolerates missing data
    # (yfinance raises various exceptions on tickers without analyst coverage).
    from datetime import UTC as _UTC  # noqa: PLC0415
    from datetime import datetime as _dt  # noqa: PLC0415

    from glostat.data.yfinance_types import (  # noqa: PLC0415
        AnalystRecommendationEvent,
    )
    ticker_obj = yf.Ticker(ticker.upper())
    try:
        df = ticker_obj.upgrades_downgrades
    except (AttributeError, TypeError, ValueError, KeyError):
        return []
    if df is None or len(df) == 0:
        return []
    out: list[AnalystRecommendationEvent] = []
    try:
        records = df.reset_index().to_dict("records")
    except (AttributeError, TypeError):
        return []
    for row in records:
        ts_raw = row.get("GradeDate") or row.get("Date") or row.get("index")
        try:
            if hasattr(ts_raw, "to_pydatetime"):
                ts = ts_raw.to_pydatetime()
            elif isinstance(ts_raw, _dt):
                ts = ts_raw
            else:
                continue
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=_UTC)
        except (AttributeError, TypeError, ValueError):
            continue
        firm = str(row.get("Firm") or "").strip()
        from_grade = str(row.get("FromGrade") or "").strip()
        to_grade = str(row.get("ToGrade") or "").strip()
        action = str(row.get("Action") or "").strip().lower()
        out.append(AnalystRecommendationEvent(
            ts=ts, firm=firm,
            from_grade=from_grade, to_grade=to_grade,
            action=action,
        ))
    return out


def recommendations_to_payload(history: Any) -> dict[str, Any]:
    return {
        "ticker": history.ticker,
        "events": [
            {
                "ts": e.ts.isoformat(),
                "firm": e.firm,
                "from_grade": e.from_grade,
                "to_grade": e.to_grade,
                "action": e.action,
            }
            for e in history.events
        ],
    }


def parse_earnings_calendar(yf: Any, ticker: str) -> list[EarningsEvent]:
    # WHY: combine future events from .calendar + recent history from .earnings_dates;
    # both surfaces are unstable in yfinance, so handle each independently.
    ticker_obj = yf.Ticker(ticker.upper())
    out: list[EarningsEvent] = []
    out.extend(_extract_calendar_events(ticker_obj, ticker.upper()))
    out.extend(_extract_history_events(ticker_obj, ticker.upper()))
    seen: set[str] = set()
    unique: list[EarningsEvent] = []
    for ev in out:
        key = ev.earnings_date.date().isoformat()
        if key in seen:
            continue
        seen.add(key)
        unique.append(ev)
    unique.sort(key=lambda e: e.earnings_date)
    return unique


def _extract_calendar_events(ticker_obj: Any, sym: str) -> list[EarningsEvent]:
    out: list[EarningsEvent] = []
    try:
        cal = ticker_obj.calendar
    except (AttributeError, TypeError, ValueError):
        return out
    if cal is None:
        return out
    raw_date: Any = None
    eps_est: Any = None
    rev_est: Any = None
    if isinstance(cal, dict):
        raw_date = cal.get("Earnings Date") or cal.get("earningsDate")
        eps_est = cal.get("Earnings Average") or cal.get("EPS Estimate")
        rev_est = cal.get("Revenue Average") or cal.get("Revenue Estimate")
    else:
        try:
            raw_date = cal.loc["Earnings Date"][0] if "Earnings Date" in cal.index else None
        except (AttributeError, KeyError, IndexError, TypeError):
            raw_date = None
    if raw_date is None:
        return out
    try:
        ts_dt = as_dt(raw_date)
    except (TypeError, ValueError):
        return out
    out.append(
        EarningsEvent(
            ticker=sym,
            earnings_date=ts_dt,
            eps_estimate=as_float(eps_est),
            eps_actual=None,
            revenue_estimate=as_float(rev_est),
        )
    )
    return out


def _extract_history_events(ticker_obj: Any, sym: str) -> list[EarningsEvent]:
    out: list[EarningsEvent] = []
    try:
        df = ticker_obj.earnings_dates
    except (AttributeError, TypeError, ValueError):
        return out
    if df is None:
        return out
    try:
        iterator = df.iterrows()
    except (AttributeError, TypeError):
        return out
    for ts, row in iterator:
        try:
            pyts = ts.to_pydatetime() if hasattr(ts, "to_pydatetime") else ts
            if isinstance(pyts, str):
                pyts = datetime.fromisoformat(pyts)
            if pyts.tzinfo is None:
                pyts = pyts.replace(tzinfo=UTC)
            out.append(
                EarningsEvent(
                    ticker=sym,
                    earnings_date=pyts,
                    eps_estimate=as_float(row.get("EPS Estimate")),
                    eps_actual=as_float(row.get("Reported EPS")),
                    revenue_estimate=as_float(row.get("Revenue Estimate")),
                )
            )
        except (KeyError, TypeError, ValueError, AttributeError):
            continue
    return out


def parse_holders(yf: Any, ticker: str, kind: HoldersKind) -> list[tuple[str, float, int, str]]:
    # WHY: yfinance returns a DataFrame whose columns shift across versions. Probe a few
    # plausible names and gracefully degrade to (name, pct) tuples augmented with shares
    # and reported_at timestamp when present.
    obj = yf.Ticker(ticker.upper())
    df = _holders_for_kind(obj, kind)
    if df is None:
        return []
    try:
        iterator = df.iterrows()
    except (AttributeError, TypeError):
        return []
    out: list[tuple[str, float, int, str]] = []
    for _, row in iterator:
        try:
            name = _holder_name(row)
            pct = _pick_float(row, ("% Out", "pctHeld", "pctOfShares"))
            shares = _pick_int(row, ("Shares", "shares"))
            ts = _pick_text(row, ("Date Reported", "dateReported"))
            if name is None:
                continue
            out.append((name, pct or 0.0, shares or 0, ts or ""))
        except (KeyError, TypeError, ValueError, AttributeError):
            continue
    return out


def _holders_for_kind(obj: Any, kind: HoldersKind) -> Any:
    try:
        if kind == "institutional":
            return obj.institutional_holders
        if kind == "major":
            return obj.major_holders
        if kind == "mutualfund":
            return obj.mutualfund_holders
        if kind == "insider":
            return obj.insider_purchases
    except (AttributeError, TypeError, ValueError):
        return None
    return None


def _holder_name(row: Any) -> str | None:
    for key in ("Holder", "holder", "Name", "name"):
        try:
            v = row[key] if not hasattr(row, "get") else row.get(key)
        except (KeyError, TypeError):
            v = None
        if v is not None and str(v).strip():
            return str(v).strip()
    return None


def _pick_float(row: Any, keys: tuple[str, ...]) -> float | None:
    for k in keys:
        try:
            v = row[k] if not hasattr(row, "get") else row.get(k)
        except (KeyError, TypeError):
            v = None
        if v is None:
            continue
        f = as_float(v)
        if f is not None:
            return f
    return None


def _pick_int(row: Any, keys: tuple[str, ...]) -> int | None:
    f = _pick_float(row, keys)
    if f is None:
        return None
    try:
        return int(f)
    except (TypeError, ValueError):
        return None


def _pick_text(row: Any, keys: tuple[str, ...]) -> str | None:
    for k in keys:
        try:
            v = row[k] if not hasattr(row, "get") else row.get(k)
        except (KeyError, TypeError):
            v = None
        if v is None:
            continue
        if hasattr(v, "isoformat"):
            return v.isoformat()
        return str(v)
    return None


def holders_to_payload(s: HoldersSnapshot) -> dict[str, Any]:
    # Sprint 5 PR #1: serialise shares + date_reported so prior snapshots
    # rehydrated from the broker drive the E_FUND_FLOW delta classifier.
    by_name = {name: (shares, ts) for (name, _pct, shares, ts) in s.rows}
    return {
        "ticker": s.ticker, "kind": s.kind,
        "fetched_at": s.fetched_at.isoformat(),
        "holders": [
            {"name": name, "pct_held": pct,
             "shares": int(by_name.get(name, (0, ""))[0] or 0),
             "date_reported": str(by_name.get(name, (0, ""))[1] or "")}
            for (name, pct) in s.holders
        ],
    }


def ohlcv_to_payload(s: OhlcvSeries) -> dict[str, Any]:
    return {
        "ticker": s.ticker,
        "interval": s.interval,
        "bars": [
            {
                "ts": b.ts.isoformat(),
                "open": b.open,
                "high": b.high,
                "low": b.low,
                "close": b.close,
                "volume": b.volume,
                "adj_close": b.adj_close,
            }
            for b in s.bars
        ],
    }


def fundamentals_to_payload(f: Fundamentals) -> dict[str, Any]:
    return {
        "ticker": f.ticker,
        "pe_ratio": f.pe_ratio,
        "forward_pe": f.forward_pe,
        "eps": f.eps,
        "forward_eps": f.forward_eps,
        "roe": f.roe,
        "market_cap": f.market_cap,
        "dividend_yield": f.dividend_yield,
        "beta": f.beta,
        "fifty_two_week_high": f.fifty_two_week_high,
        "fifty_two_week_low": f.fifty_two_week_low,
    }


def dividends_to_payload(h: DividendHistory) -> dict[str, Any]:
    return {
        "ticker": h.ticker,
        "events": [
            {"ex_date": e.ex_date.isoformat(), "amount": e.amount}
            for e in h.events
        ],
    }


def earnings_to_payload(c: EarningsCalendar) -> dict[str, Any]:
    return {
        "ticker": c.ticker,
        "upcoming": [
            {
                "earnings_date": e.earnings_date.isoformat(),
                "eps_estimate": e.eps_estimate,
                "eps_actual": e.eps_actual,
                "revenue_estimate": e.revenue_estimate,
            }
            for e in c.upcoming
        ],
    }


__all__ = [
    "as_dt",
    "as_float",
    "coerce_date",
    "dividends_to_payload",
    "earnings_to_payload",
    "fundamentals_to_payload",
    "holders_to_payload",
    "ohlcv_to_payload",
    "parse_dividends",
    "parse_earnings_calendar",
    "parse_fundamentals",
    "parse_holders",
    "parse_ohlcv",
    "parse_recommendations",
    "recommendations_to_payload",
]
