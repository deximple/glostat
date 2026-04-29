from __future__ import annotations

import pytest

from glostat.core.errors import ConfigError
from glostat.data.data_router import (
    DataRouter,
    is_kr_ticker,
    normalize_kr_ticker,
    to_yfinance_kr_ticker,
)

# ── helper: is_kr_ticker / normalize / to_yfinance ────────────────────────


def test_is_kr_ticker_accepts_six_digit() -> None:
    assert is_kr_ticker("005930") is True
    assert is_kr_ticker("096770") is True


def test_is_kr_ticker_accepts_ks_suffix() -> None:
    assert is_kr_ticker("005930.KS") is True
    assert is_kr_ticker("005930.kq") is True   # case-insensitive


def test_is_kr_ticker_rejects_us_tickers() -> None:
    assert is_kr_ticker("AAPL") is False
    assert is_kr_ticker("MSFT") is False
    assert is_kr_ticker("BRK.B") is False


def test_is_kr_ticker_rejects_short_or_long_codes() -> None:
    assert is_kr_ticker("12345") is False     # 5 digits
    assert is_kr_ticker("1234567") is False   # 7 digits
    assert is_kr_ticker("") is False


def test_is_kr_ticker_rejects_alpha_six_chars() -> None:
    assert is_kr_ticker("ABCDEF") is False


def test_normalize_strips_ks_suffix() -> None:
    assert normalize_kr_ticker("005930.KS") == "005930"
    assert normalize_kr_ticker("096770.kq") == "096770"


def test_normalize_passes_through_us_tickers() -> None:
    assert normalize_kr_ticker("AAPL") == "AAPL"


def test_to_yfinance_appends_ks_for_bare_kr_code() -> None:
    assert to_yfinance_kr_ticker("005930") == "005930.KS"
    assert to_yfinance_kr_ticker("096770") == "096770.KS"


def test_to_yfinance_passes_through_already_suffixed() -> None:
    assert to_yfinance_kr_ticker("005930.KS") == "005930.KS"
    assert to_yfinance_kr_ticker("005930.KQ") == "005930.KQ"


def test_to_yfinance_passes_through_us_tickers() -> None:
    assert to_yfinance_kr_ticker("AAPL") == "AAPL"


def test_to_yfinance_kosdaq_suffix() -> None:
    assert to_yfinance_kr_ticker("123456", default_suffix=".KQ") == "123456.KQ"


# ── DataRouter routing for KR routes ──────────────────────────────────────


class _StubYf:
    pass


class _StubNaver:
    async def fetch_history(self, code: str, **kwargs: object) -> list[object]:
        return []


def test_router_routes_e_fundamental_kr_to_yfinance() -> None:
    r = DataRouter()
    yf = _StubYf()
    r.register_client("yfinance", yf)
    client, method = r.route("E_FUNDAMENTAL_KR", "fundamentals")
    assert client is yf
    assert method == "get_fundamentals"


def test_router_routes_e_foreign_reversal_to_naver() -> None:
    r = DataRouter()
    naver = _StubNaver()
    r.register_client("naver_kr", naver)
    client, method = r.route("E_FOREIGN_REVERSAL", "naver_flows")
    assert client is naver
    assert method == "fetch_history"


def test_router_raises_when_naver_not_registered() -> None:
    r = DataRouter()
    with pytest.raises(ConfigError) as exc:
        r.route("E_FOREIGN_REVERSAL", "naver_flows")
    assert "naver_kr" in str(exc.value)


def test_router_raises_for_unknown_kr_data_type() -> None:
    r = DataRouter()
    with pytest.raises(ConfigError):
        r.route("E_FUNDAMENTAL_KR", "no_such_data_type")
