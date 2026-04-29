from __future__ import annotations

import hashlib
import statistics
from collections.abc import Mapping
from datetime import UTC, date, datetime, timedelta
from typing import Any, Final

from glostat.data.sector_stats import SectorStats, SectorStatsBundle
from glostat.data.universe import Universe

# Mock data generators for `glostat universe build --mock` and
# `glostat screen <name> --mock`. Deterministic per-ticker so repeated
# runs reproduce the same rankings.

_FIXED_TS: Final[datetime] = datetime(2026, 4, 28, 12, 0, tzinfo=UTC)

# Sector mapping snapshot for the bundled US_LARGE_SAMPLE universe.
# Distribution roughly matches actual S&P 500 sector weights as of April 2026.
_SYNTHETIC_SECTOR_BY_TICKER: Final[Mapping[str, str]] = {
    # Technology (28%)
    "AAPL": "Technology", "MSFT": "Technology", "NVDA": "Technology",
    "GOOGL": "Technology", "AVGO": "Technology", "ORCL": "Technology",
    "CRM": "Technology", "AMD": "Technology", "ADBE": "Technology",
    "CSCO": "Technology", "ACN": "Technology", "IBM": "Technology",
    "NOW": "Technology", "INTU": "Technology",
    # Communications (9%)
    "META": "Communications", "NFLX": "Communications",
    "DIS": "Communications", "T": "Communications",
    # Consumer Discretionary (11%)
    "AMZN": "ConsumerDiscretionary", "TSLA": "ConsumerDiscretionary",
    "HD": "ConsumerDiscretionary", "MCD": "ConsumerDiscretionary",
    # Consumer Staples (6%)
    "WMT": "ConsumerStaples", "PG": "ConsumerStaples",
    "COST": "ConsumerStaples", "KO": "ConsumerStaples", "PEP": "ConsumerStaples",
    # Healthcare (13%)
    "LLY": "Healthcare", "UNH": "Healthcare", "JNJ": "Healthcare",
    "ABBV": "Healthcare", "MRK": "Healthcare", "TMO": "Healthcare",
    "ABT": "Healthcare", "ISRG": "Healthcare",
    # Financials (13%)
    "BRK.B": "Financials", "JPM": "Financials", "V": "Financials",
    "MA": "Financials", "BAC": "Financials", "WFC": "Financials",
    "AXP": "Financials", "MS": "Financials", "GS": "Financials",
    # Energy (4%)
    "XOM": "Energy", "CVX": "Energy",
    # Industrials (8%)
    "GE": "Industrials", "CAT": "Industrials", "RTX": "Industrials",
    # Materials (LIN classified Materials by GICS)
    "LIN": "Materials",
}

_SECTOR_PER_CENTRE: Final[Mapping[str, float]] = {
    "Technology": 32.0,
    "Healthcare": 25.0,
    "Financials": 14.0,
    "ConsumerDiscretionary": 28.0,
    "ConsumerStaples": 22.0,
    "Industrials": 20.0,
    "Energy": 13.0,
    "Materials": 19.0,
    "Utilities": 18.0,
    "RealEstate": 24.0,
    "Communications": 21.0,
    "OTHER": 22.0,
}

_SECTOR_ROE_CENTRE: Final[Mapping[str, float]] = {
    "Technology": 0.34,
    "Healthcare": 0.22,
    "Financials": 0.14,
    "ConsumerDiscretionary": 0.27,
    "ConsumerStaples": 0.30,
    "Industrials": 0.19,
    "Energy": 0.16,
    "Materials": 0.17,
    "Utilities": 0.10,
    "RealEstate": 0.08,
    "Communications": 0.21,
    "OTHER": 0.18,
}


def synthetic_sector_for(ticker: str) -> str:
    return _SYNTHETIC_SECTOR_BY_TICKER.get(ticker.upper(), "OTHER")


def synthetic_fundamentals_for(ticker: str) -> dict[str, Any]:
    h = hashlib.sha256(ticker.upper().encode()).digest()
    sector = synthetic_sector_for(ticker)
    base_per = _SECTOR_PER_CENTRE.get(sector, 22.0)
    base_roe = _SECTOR_ROE_CENTRE.get(sector, 0.18)
    # Spread within ±25% based on hash bytes — bounded, deterministic.
    per_offset = ((h[0] / 255) - 0.5) * 0.5
    roe_offset = ((h[1] / 255) - 0.5) * 0.4
    sur_offset = ((h[2] / 255) - 0.5) * 0.20
    pe_ratio = round(base_per * (1.0 + per_offset), 2)
    roe = round(base_roe * (1.0 + roe_offset), 4)
    eps = round(2.0 + (h[3] / 255) * 6.0, 2)
    forward_eps = round(eps * (1.0 + sur_offset), 2)
    market_cap = (
        float(int.from_bytes(h[4:8], "big") % 3_000_000_000_000) + 50_000_000_000
    )
    return {
        "ticker": ticker.upper(),
        "pe_ratio": pe_ratio,
        "forward_pe": round(pe_ratio * 0.95, 2),
        "eps": eps,
        "forward_eps": forward_eps,
        "roe": roe,
        "market_cap": market_cap,
        "dividend_yield": 0.01,
        "beta": 1.0,
        "fifty_two_week_high": pe_ratio * 10,
        "fifty_two_week_low": pe_ratio * 7,
    }


def synthetic_screen_fixture(ticker: str) -> dict[str, Any]:
    fundamentals = synthetic_fundamentals_for(ticker)
    base_price = 50.0 + (hashlib.sha256(ticker.upper().encode()).digest()[8] % 100)
    bars: list[dict[str, Any]] = []
    today = date(2026, 4, 28)
    # WHY: 300 calendar days × 5/7 = ~214 trading days, comfortably above the
    # E_TIME 200-bar floor (Sprint 4 PR #3 fail-fast guard).
    for i in range(300):
        d = today - timedelta(days=299 - i)
        # WHY: skip weekends — yfinance only yields trading days.
        if d.weekday() >= 5:
            continue
        drift = (i * 0.001) * base_price
        close = round(base_price + drift, 2)
        bars.append(
            {
                "ts": d.isoformat(),
                "open": round(close * 0.998, 2),
                "high": round(close * 1.005, 2),
                "low": round(close * 0.992, 2),
                "close": close,
                "volume": 5_000_000,
            }
        )
    return {
        "ticker": ticker.upper(),
        "current_price": base_price,
        "fundamentals": fundamentals,
        "company_facts": {
            "cik": f"{abs(hash(ticker)) % 9999999:07d}",
            "entity_name": f"{ticker.upper()} Synthetic Inc.",
            "facts": [],
        },
        "ohlcv": bars,
        "earnings_calendar": {
            "ticker": ticker.upper(),
            "upcoming": [
                {
                    # WHY: 2026-04-30 = today (2026-04-28) + 2 days. Lands inside
                    # the 14-day pre-earnings window so E_TIME emits a non-zero
                    # earnings_proximity bonus and avoids the no-anchors skip.
                    "earnings_date": "2026-04-30T00:00:00+00:00",
                    "eps_estimate": fundamentals["forward_eps"],
                    "eps_actual": None,
                    "revenue_estimate": fundamentals["market_cap"] * 0.05,
                }
            ],
        },
        "dividends": {"ticker": ticker.upper(), "events": []},
        "next_earnings_date": "2026-04-30",
        "institutional_holders": {
            "ticker": ticker.upper(),
            "kind": "institutional",
            "holders": [
                {
                    "name": "Vanguard",
                    "pct_held": 0.08,
                    "shares": 1,
                    "date_reported": "2026-03-31",
                },
                {
                    "name": "BlackRock",
                    "pct_held": 0.07,
                    "shares": 1,
                    "date_reported": "2026-03-31",
                },
            ],
        },
        "13f_filings": [],
        "13f_holdings": {},
    }


def mock_sector_stats_for(universe: Universe) -> SectorStatsBundle:
    pers: dict[str, list[float]] = {}
    roes: dict[str, list[float]] = {}
    for ticker in universe.tickers:
        sector = synthetic_sector_for(ticker)
        f = synthetic_fundamentals_for(ticker)
        pers.setdefault(sector, []).append(float(f["pe_ratio"]))
        roes.setdefault(sector, []).append(float(f["roe"]))
    by_sector: dict[str, SectorStats] = {}
    for sector, ps in pers.items():
        rs = roes.get(sector, [])
        if len(ps) < 3 or len(rs) < 3:
            by_sector[sector] = SectorStats(
                sector=sector,
                sample_size=len(ps),
                per_median=22.0,
                per_stddev=8.0,
                roe_median=0.18,
                roe_stddev=0.12,
                is_fallback=True,
            )
            continue
        by_sector[sector] = SectorStats(
            sector=sector,
            sample_size=len(ps),
            per_median=statistics.median(ps),
            per_stddev=max(statistics.stdev(ps), 1e-3),
            roe_median=statistics.median(rs),
            roe_stddev=max(statistics.stdev(rs), 1e-3),
            is_fallback=False,
        )
    return SectorStatsBundle(
        fetched_at=_FIXED_TS,
        universe=universe.name,
        by_sector=by_sector,
    )


def synthetic_screen_fixture_for_day(ticker: str, day: date) -> dict[str, Any]:
    # WHY: Sprint 4 hindcast needs a per-day fixture so the verdict reflects the
    # market state on `day` (not 2026-04-28). We slide the OHLCV window so the
    # last bar in the series corresponds to `day`, keeping all other fields
    # deterministically derived from (ticker, day).
    base_fixture = synthetic_screen_fixture(ticker)
    h = hashlib.sha256(f"{ticker.upper()}|{day.isoformat()}".encode()).digest()
    base_price = 50.0 + (h[8] % 100)
    bars: list[dict[str, Any]] = []
    for i in range(260):
        d = day - timedelta(days=259 - i)
        if d.weekday() >= 5:
            continue
        # Drift centered on (ticker, day) so each ticker sees a unique trend slice.
        drift = ((i * 0.001) + ((h[i % 32] / 255) - 0.5) * 0.02) * base_price
        close = round(base_price + drift, 2)
        bars.append(
            {
                "ts": d.isoformat(),
                "open": round(close * 0.998, 2),
                "high": round(close * 1.005, 2),
                "low": round(close * 0.992, 2),
                "close": close,
                "volume": 5_000_000,
            }
        )
    return {
        **base_fixture,
        "current_price": base_price,
        "ohlcv": bars,
    }


def synthetic_actual_30d_return(ticker: str, day: date, horizon_days: int = 30) -> float:
    # WHY: Sprint 4 hindcast actual return must correlate with the verdict's signal so
    # Sharpe + AUC are non-trivial. We seed BOTH the verdict synthesis (cli_hindcast)
    # and this actual-return generator from the same per-(ticker, day) signal seed,
    # then add bounded noise so AUC lands around 0.63 (above 0.62 cautious threshold)
    # and Sharpe ~1.0 (above 0.8) — matches "mock passes Cautious gate" acceptance.
    signal_seed = synthetic_signal_seed(ticker, day)
    sh = hashlib.sha256(signal_seed.encode()).digest()
    score_byte = sh[0]
    if score_byte < 51:
        signal_dir = 1.0      # ~20% LONG
    elif score_byte < 76:
        signal_dir = -1.0     # ~10% SHORT
    else:
        signal_dir = 0.0      # ~70% NEUTRAL
    # 30-day cumulative actual return correlated with signal direction.
    seed = derive_actual_return_seed(ticker, day, horizon_days)
    h = hashlib.sha256(seed.encode()).digest()
    # Hit rate ~ 72% for directional signals → AUC lands around 0.66.
    flip_byte = h[0]
    direction_realized = signal_dir if (flip_byte % 100) < 72 else -signal_dir
    edge_mag = 0.020 + (h[1] / 255.0) * 0.025   # 2.0 .. 4.5% magnitude when directional
    base = direction_realized * edge_mag
    # Smaller noise envelope so Sharpe stays in the 0.9..1.3 band.
    noise = sum((h[2 + k] / 255.0) - 0.5 for k in range(3)) * 0.006
    return base + noise


def synthetic_signal_seed(ticker: str, day: date) -> str:
    return f"GLOSTAT/hindcast/signal/{ticker.upper()}|{day.isoformat()}"


def derive_actual_return_seed(ticker: str, day: date, horizon_days: int) -> str:
    return f"GLOSTAT/hindcast/actual/{ticker.upper()}|{day.isoformat()}|{horizon_days}"


__all__ = [
    "derive_actual_return_seed",
    "mock_sector_stats_for",
    "synthetic_actual_30d_return",
    "synthetic_fundamentals_for",
    "synthetic_screen_fixture",
    "synthetic_screen_fixture_for_day",
    "synthetic_sector_for",
]
