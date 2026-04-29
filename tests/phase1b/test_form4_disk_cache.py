from __future__ import annotations

from datetime import date
from pathlib import Path

from glostat.data.sec_edgar_form4 import Form4Transaction
from glostat.phase1b.form4_disk_cache import cache_path, load, save


def _txn(d: date, code: str = "P") -> Form4Transaction:
    return Form4Transaction(
        issuer_cik="0001",
        accession="acc-1",
        filed_at=date(2024, 6, 1),
        transaction_date=d,
        reporter_name="A",
        reporter_cik="100",
        reporter_role="Director",
        code=code,
        shares=100.0,
        price=10.0,
        value_usd=1000.0,
    )


def test_save_and_load_round_trip(tmp_path: Path):
    txns = [_txn(date(2024, 6, 10)), _txn(date(2024, 6, 11), code="S")]
    save("TEST", 60, txns, base=tmp_path)
    p = cache_path("TEST", 60, base=tmp_path)
    assert p.exists()
    loaded = load("TEST", 60, base=tmp_path)
    assert loaded is not None
    assert len(loaded) == 2
    assert loaded[0].transaction_date == date(2024, 6, 10)
    assert loaded[0].code == "P"
    assert loaded[1].code == "S"


def test_load_returns_none_when_missing(tmp_path: Path):
    assert load("MISSING", 60, base=tmp_path) is None


def test_load_handles_corrupt_file(tmp_path: Path):
    p = cache_path("BAD", 30, base=tmp_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("not json")
    assert load("BAD", 30, base=tmp_path) is None
