from __future__ import annotations

from datetime import date

from glostat.data.toss_client import (
    TossClient,
    TossInvestorBar,
    _coerce_date,
    _normalize_code,
)


def test_normalize_code_strips_suffix() -> None:
    assert _normalize_code("005930.KS") == "005930"
    assert _normalize_code("005930.KQ") == "005930"
    assert _normalize_code("005930") == "005930"


def test_coerce_date_handles_iso_string() -> None:
    assert _coerce_date("2026-04-15") == date(2026, 4, 15)


def test_coerce_date_handles_date_object() -> None:
    d = date(2026, 4, 15)
    assert _coerce_date(d) == d


def test_coerce_date_returns_none_on_garbage() -> None:
    assert _coerce_date("not a date") is None
    assert _coerce_date(None) is None
    assert _coerce_date("") is None


def test_is_available_false_when_cache_missing(tmp_path) -> None:
    client = TossClient(cache_dir=tmp_path)
    assert client.is_available("005930") is False


def test_load_returns_empty_when_cache_missing(tmp_path) -> None:
    client = TossClient(cache_dir=tmp_path)
    assert client.load_investor_trend("005930") == []


def test_save_and_load_roundtrip(tmp_path) -> None:
    client = TossClient(cache_dir=tmp_path)
    bars = [
        TossInvestorBar(
            bar_date=date(2026, 4, 1), ticker="005930",
            foreign_net_won=1.0e9, institutional_net_won=-2.0e8,
            retail_net_won=5.0e7,
        ),
        TossInvestorBar(
            bar_date=date(2026, 4, 2), ticker="005930",
            foreign_net_won=-3.0e8, institutional_net_won=4.0e8,
            retail_net_won=-1.0e8,
        ),
    ]
    client.save_investor_trend("005930", bars)
    assert client.is_available("005930") is True
    loaded = client.load_investor_trend("005930")
    assert len(loaded) == 2
    assert loaded[0].bar_date == date(2026, 4, 1)
    assert loaded[0].foreign_net_won == 1.0e9
    assert loaded[1].institutional_net_won == 4.0e8


def test_save_empty_list_no_op(tmp_path) -> None:
    client = TossClient(cache_dir=tmp_path)
    path = client.save_investor_trend("005930", [])
    # Path is returned but file isn't actually created when bars are empty.
    assert path.parent == tmp_path
    assert not path.exists()


def test_load_with_days_back_filter(tmp_path) -> None:
    client = TossClient(cache_dir=tmp_path)
    bars = [
        TossInvestorBar(
            bar_date=date(2026, 1, 1), ticker="005930",
            foreign_net_won=0.0, institutional_net_won=0.0, retail_net_won=0.0,
        ),
        TossInvestorBar(
            bar_date=date(2026, 4, 14), ticker="005930",
            foreign_net_won=0.0, institutional_net_won=0.0, retail_net_won=0.0,
        ),
        TossInvestorBar(
            bar_date=date(2026, 4, 15), ticker="005930",
            foreign_net_won=0.0, institutional_net_won=0.0, retail_net_won=0.0,
        ),
    ]
    client.save_investor_trend("005930", bars)
    # days_back=5 — only the most recent two should survive (Apr 14 + Apr 15).
    loaded = client.load_investor_trend("005930", days_back=5)
    assert len(loaded) == 2
    assert all(b.bar_date >= date(2026, 4, 10) for b in loaded)


def test_cache_path_uses_normalized_ticker(tmp_path) -> None:
    client = TossClient(cache_dir=tmp_path)
    p_ks = client.cache_path("005930.KS")
    p_bare = client.cache_path("005930")
    assert p_ks == p_bare
    assert p_ks.name == "005930.parquet"


def test_load_corrupted_parquet_returns_empty(tmp_path) -> None:
    # Write garbage to the expected parquet path; loader should swallow the error.
    client = TossClient(cache_dir=tmp_path)
    path = client.cache_path("005930")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"not a parquet file")
    assert client.load_investor_trend("005930") == []
