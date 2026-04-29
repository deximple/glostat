from __future__ import annotations

from datetime import date

import pytest

from glostat.data.sec_edgar_form4 import (
    cluster_buy_count,
    cluster_buy_value,
    parse_form4_xml,
)

# Mock Form 4 XML — minimum valid structure for SEC's reporting schema.
_FORM4_XML = """<?xml version="1.0"?>
<ownershipDocument>
  <reportingOwner>
    <reportingOwnerId>
      <rptOwnerCik>0001234567</rptOwnerCik>
      <rptOwnerName>SMITH JOHN</rptOwnerName>
    </reportingOwnerId>
    <reportingOwnerRelationship>
      <isDirector>true</isDirector>
      <isOfficer>0</isOfficer>
      <isTenPercentOwner>0</isTenPercentOwner>
    </reportingOwnerRelationship>
  </reportingOwner>
  <nonDerivativeTable>
    <nonDerivativeTransaction>
      <transactionDate><value>2024-06-15</value></transactionDate>
      <transactionCoding>
        <transactionCode>P</transactionCode>
      </transactionCoding>
      <transactionAmounts>
        <transactionShares><value>1000</value></transactionShares>
        <transactionPricePerShare><value>25.50</value></transactionPricePerShare>
      </transactionAmounts>
    </nonDerivativeTransaction>
    <nonDerivativeTransaction>
      <transactionDate><value>2024-06-15</value></transactionDate>
      <transactionCoding>
        <transactionCode>S</transactionCode>
      </transactionCoding>
      <transactionAmounts>
        <transactionShares><value>500</value></transactionShares>
        <transactionPricePerShare><value>26.00</value></transactionPricePerShare>
      </transactionAmounts>
    </nonDerivativeTransaction>
    <nonDerivativeTransaction>
      <transactionDate><value>2024-06-15</value></transactionDate>
      <transactionCoding>
        <transactionCode>A</transactionCode>
      </transactionCoding>
      <transactionAmounts>
        <transactionShares><value>200</value></transactionShares>
        <transactionPricePerShare><value>0</value></transactionPricePerShare>
      </transactionAmounts>
    </nonDerivativeTransaction>
  </nonDerivativeTable>
</ownershipDocument>
"""


def test_parse_form4_xml_extracts_buys_and_sells():
    txns = parse_form4_xml(
        _FORM4_XML,
        issuer_cik="0001000000",
        accession="0001-24-000001",
        filed_at=date(2024, 6, 17),
    )
    # Code A is filtered out (grant/award only)
    assert len(txns) == 2
    buy = next(t for t in txns if t.code == "P")
    sell = next(t for t in txns if t.code == "S")
    assert buy.is_buy and not buy.is_sell
    assert sell.is_sell and not sell.is_buy
    assert buy.shares == 1000.0
    assert buy.price == 25.5
    assert buy.value_usd == 25500.0
    assert buy.reporter_role == "Director"
    assert buy.reporter_name == "SMITH JOHN"


def test_parse_form4_xml_handles_garbage():
    assert parse_form4_xml("", issuer_cik="x", accession="y", filed_at=date(2024, 1, 1)) == []
    assert parse_form4_xml("not xml", issuer_cik="x", accession="y", filed_at=date(2024, 1, 1)) == []
    assert parse_form4_xml(
        "<bad><unclosed>",
        issuer_cik="x", accession="y", filed_at=date(2024, 1, 1),
    ) == []


def test_cluster_buy_count_unique_reporters():
    from glostat.data.sec_edgar_form4 import Form4Transaction

    base = dict(
        issuer_cik="x", accession="a", filed_at=date(2024, 6, 1),
        reporter_role="Director", code="P", shares=100.0, price=10.0, value_usd=1000.0,
    )
    txns = [
        Form4Transaction(transaction_date=date(2024, 6, 10), reporter_name="A", reporter_cik="1", **base),
        Form4Transaction(transaction_date=date(2024, 6, 11), reporter_name="B", reporter_cik="2", **base),
        Form4Transaction(transaction_date=date(2024, 6, 12), reporter_name="A", reporter_cik="1", **base),
        Form4Transaction(transaction_date=date(2024, 6, 13), reporter_name="C", reporter_cik="3", **base),
    ]
    assert cluster_buy_count(txns, window_end=date(2024, 6, 14), window_days=14) == 3
    assert cluster_buy_count(txns, window_end=date(2024, 5, 14), window_days=14) == 0


def test_cluster_buy_value_sums_only_buys_in_window():
    from glostat.data.sec_edgar_form4 import Form4Transaction

    base = dict(
        issuer_cik="x", accession="a", filed_at=date(2024, 6, 1),
        reporter_role="Director", reporter_name="A", reporter_cik="1",
        shares=100.0, price=10.0,
    )
    txns = [
        Form4Transaction(transaction_date=date(2024, 6, 10), code="P", value_usd=1000.0, **base),
        Form4Transaction(transaction_date=date(2024, 6, 11), code="S", value_usd=500.0, **base),
        Form4Transaction(transaction_date=date(2024, 6, 12), code="P", value_usd=2000.0, **base),
    ]
    total = cluster_buy_value(txns, window_end=date(2024, 6, 14), window_days=14)
    assert total == 3000.0


@pytest.mark.parametrize("days_back,expected", [(1, 1), (5, 1), (30, 2)])
def test_cluster_buy_count_window_size(days_back, expected):
    from glostat.data.sec_edgar_form4 import Form4Transaction

    base = dict(
        issuer_cik="x", accession="a", filed_at=date(2024, 6, 1),
        reporter_role="Director", code="P", shares=100.0, price=10.0, value_usd=1000.0,
    )
    txns = [
        Form4Transaction(transaction_date=date(2024, 6, 5), reporter_name="A", reporter_cik="1", **base),
        Form4Transaction(transaction_date=date(2024, 5, 25), reporter_name="B", reporter_cik="2", **base),
    ]
    assert cluster_buy_count(txns, window_end=date(2024, 6, 6), window_days=days_back) == expected
