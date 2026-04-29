from __future__ import annotations

import io
import zipfile
from datetime import date
from pathlib import Path

import pytest

from glostat.data.cftc_client import (
    CONTRACT_PATTERNS,
    CftcClient,
    CftcDataError,
    CotRecord,
    _match_contract,
    _parse_csv,
    _to_int,
    commercial_net_percentile,
)

# Synthetic CSV — matches the cftc.gov column order. We only populate the
# fields the parser actually reads (ix 0,2,7,8,9,11,12).

_HEADER_LINE = (
    '"Market and Exchange Names","As of Date in Form YYMMDD",'
    '"As of Date in Form YYYY-MM-DD","CFTC Contract Market Code",'
    '"CFTC Market Code in Initials","CFTC Region Code","CFTC Commodity Code",'
    '"Open Interest (All)","Noncommercial Positions-Long (All)",'
    '"Noncommercial Positions-Short (All)","Noncommercial Positions-Spreading (All)",'
    '"Commercial Positions-Long (All)","Commercial Positions-Short (All)"'
)


def _row(market: str, ymd: str, oi: int, nc_l: int, nc_s: int,
         comm_l: int, comm_s: int) -> str:
    pad_after = ',' * 100
    return (
        f'"{market}","251230",{ymd},"001602","CBT","00","001",'
        f'{oi},{nc_l},{nc_s},0,{comm_l},{comm_s}{pad_after}'
    )


def test_match_contract_known_patterns() -> None:
    assert _match_contract("WTI FINANCIAL CRUDE OIL - NYMEX") is None  # excluded
    assert _match_contract("CRUDE OIL, LIGHT SWEET-WTI - ICE") == "WTI_CRUDE"
    assert _match_contract("GOLD - COMMODITY EXCHANGE INC.") == "GOLD"
    assert _match_contract("SILVER - COMMODITY EXCHANGE INC.") == "SILVER"
    assert _match_contract("COPPER- #1 - COMMODITY EXCHANGE INC.") == "COPPER"
    assert _match_contract("CORN - CHICAGO BOARD OF TRADE") == "CORN"
    assert _match_contract("WHEAT-SRW - CHICAGO BOARD OF TRADE") == "WHEAT"
    assert _match_contract("WHEAT-HRW - CHICAGO BOARD OF TRADE") is None
    assert _match_contract("NAT GAS NYME - NEW YORK MERCANTILE EXCHANGE") == "NAT_GAS"
    assert _match_contract("FROZEN ORANGE JUICE") is None


def test_to_int_handles_formatting_quirks() -> None:
    assert _to_int("100,000") == 100_000
    assert _to_int("-") == 0
    assert _to_int("") == 0
    assert _to_int("nan") == 0
    assert _to_int("123.0") == 123


def test_parse_csv_filters_to_canonical_contracts() -> None:
    rows = "\n".join([
        _HEADER_LINE,
        _row("FROZEN ORANGE JUICE", "2024-01-09", 100, 10, 20, 30, 40),
        _row("GOLD - COMMODITY EXCHANGE INC.", "2024-01-09", 500_000,
             100_000, 50_000, 200_000, 250_000),
        _row("CRUDE OIL, LIGHT SWEET-WTI - ICE", "2024-01-09", 1_000_000,
             400_000, 300_000, 500_000, 600_000),
    ])
    parsed = _parse_csv(rows)
    assert {p.contract for p in parsed} == {"GOLD", "WTI_CRUDE"}
    gold = next(p for p in parsed if p.contract == "GOLD")
    assert gold.report_date == date(2024, 1, 9)
    assert gold.commercial_long == 200_000
    assert gold.commercial_short == 250_000
    assert gold.commercial_net == -50_000
    assert gold.commercial_net_pct == pytest.approx(-50_000 / 500_000)


def test_commercial_net_percentile_returns_none_when_thin() -> None:
    recs = (
        CotRecord(
            contract="GOLD", market_name="x", report_date=date(2024, 1, 1),
            open_interest=100, commercial_long=10, commercial_short=5,
            noncommercial_long=0, noncommercial_short=0,
        ),
    )
    rank = commercial_net_percentile(
        recs, contract="GOLD", as_of=date(2024, 6, 30), lookback_years=5
    )
    assert rank is None


def test_commercial_net_percentile_ranks_full_window() -> None:
    base_oi = 100
    recs: list[CotRecord] = []
    for i in range(40):
        d = date(2024, 1, 1)
        d_w = date(d.year, d.month, max(1, min(28, d.day + i)))
        recs.append(
            CotRecord(
                contract="GOLD",
                market_name="GOLD",
                report_date=d_w if i < 28 else date(2024, 1, 28),
                open_interest=base_oi,
                commercial_long=10 + i,
                commercial_short=5,
                noncommercial_long=0,
                noncommercial_short=0,
            )
        )
    # Latest commercial_net = (10+39) - 5 = 44 / 100 = 0.44 — highest of all 40.
    rank = commercial_net_percentile(
        recs, contract="GOLD", as_of=date(2024, 1, 28), lookback_years=5
    )
    assert rank is not None
    assert rank == pytest.approx(1.0)


def test_contract_patterns_table_keys_match_used_constants() -> None:
    # WHY: keep the canonical key set aligned with the experts that import it.
    expected = {
        "WTI_CRUDE", "NAT_GAS", "GOLD", "SILVER", "COPPER", "CORN", "WHEAT",
    }
    assert set(CONTRACT_PATTERNS) == expected


def test_cftc_client_parses_synthetic_zip(tmp_path: Path) -> None:
    cache = tmp_path / "cftc"
    cache.mkdir()
    csv_text = "\n".join([
        _HEADER_LINE,
        _row("GOLD - COMMODITY EXCHANGE INC.", "2024-02-06", 500, 100, 50, 200, 250),
        _row("WHEAT-SRW - CHICAGO BOARD OF TRADE", "2024-02-06", 800, 200, 150, 350, 300),
    ])
    zpath = cache / "deacot2024.zip"
    with zipfile.ZipFile(zpath, "w") as zf:
        zf.writestr("annual.txt", csv_text)
    client = CftcClient(cache_dir=cache, snapshot_broker=None)
    import asyncio
    recs = asyncio.run(client.fetch_year(2024))
    assert {r.contract for r in recs} == {"GOLD", "WHEAT"}
    asyncio.run(client.fetch_year(2024))  # idempotent — second call hits memo


def test_cftc_client_rejects_tiny_archive(tmp_path: Path) -> None:
    cache = tmp_path / "cftc"
    cache.mkdir()
    bad = cache / "deacot2099.zip"
    bad.write_bytes(b"\x00" * 32)
    client = CftcClient(cache_dir=cache)
    import asyncio
    with pytest.raises(CftcDataError):
        asyncio.run(client.fetch_year(2099))


def _stuffed_csv() -> str:
    return io.StringIO("\n".join([_HEADER_LINE])).getvalue()
