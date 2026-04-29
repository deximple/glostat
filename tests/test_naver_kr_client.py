from __future__ import annotations

from datetime import date

from glostat.data.naver_kr_client import parse_frgn_page

_SAMPLE_PAGE: str = """
<table>
<tr onMouseOver="rOver(this)" onMouseOut="rOut(this)">
    <td width="62" class="tc"><span class="tah p10 gray03">2026.04.29</span></td>
    <td width="67" class="num"><span class="tah p11">226,000</span></td>
    <td width="67" class="num">
        <em class="bu_p bu_pup"><span class="blind">상승</span></em><span class="tah p11 red02">4,000</span>
    </td>
    <td width="67" class="num"><span class="tah p11 red01">+1.80%</span></td>
    <td width="67" class="num"><span class="tah p11">20,205,287</span></td>
    <td width="66" class="num"><span class="tah p11 red01">+1,075,301</span></td>
    <td width="80" class="num"><span class="tah p11 red01">+2,028,533</span></td>
    <td width="60" class="num"><span class="tah p11">2,877,764,515</span></td>
    <td width="60" class="num"><span class="tah p11">49.22%</span></td>
</tr>
<tr onMouseOver="rOver(this)" onMouseOut="rOut(this)">
    <td width="62" class="tc"><span class="tah p10 gray03">2026.04.28</span></td>
    <td width="67" class="num"><span class="tah p11">222,000</span></td>
    <td width="67" class="num">
        <em class="bu_p bu_pdn"><span class="blind">하락</span></em><span class="tah p11 blue02">-1,000</span>
    </td>
    <td width="67" class="num"><span class="tah p11 blue01">-0.45%</span></td>
    <td width="67" class="num"><span class="tah p11">15,000,000</span></td>
    <td width="66" class="num"><span class="tah p11 blue01">-500,000</span></td>
    <td width="80" class="num"><span class="tah p11 blue01">-1,200,000</span></td>
    <td width="60" class="num"><span class="tah p11">2,875,000,000</span></td>
    <td width="60" class="num"><span class="tah p11">49.20%</span></td>
</tr>
</table>
"""


def test_parse_frgn_page_extracts_two_rows() -> None:
    bars = parse_frgn_page(_SAMPLE_PAGE, "005930")
    assert len(bars) == 2
    assert bars[0].bar_date == date(2026, 4, 29)
    assert bars[0].close_price == 226000.0
    assert bars[0].organ_net == 1075301.0
    assert bars[0].foreign_net == 2028533.0
    assert bars[0].foreign_holdings == 2877764515.0
    assert bars[0].foreign_hold_pct == 49.22


def test_parse_frgn_page_handles_negative_net_flows() -> None:
    bars = parse_frgn_page(_SAMPLE_PAGE, "005930")
    assert bars[1].bar_date == date(2026, 4, 28)
    assert bars[1].organ_net == -500000.0
    assert bars[1].foreign_net == -1200000.0


def test_parse_frgn_page_empty_html_returns_empty_list() -> None:
    assert parse_frgn_page("<html></html>", "005930") == []


def test_parse_frgn_page_skips_malformed_rows() -> None:
    bad = '<tr onMouseOver="x"><td>no spans here</td></tr>'
    assert parse_frgn_page(bad, "005930") == []
