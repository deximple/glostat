from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from glostat.data.vkospi_client import VkospiClient, VkospiDataError
from glostat.data.vkospi_csv_provider import (
    attach_csv_provider,
    make_csv_provider,
    parse_csv,
)

# ── parse_csv — accepted formats ─────────────────────────────────────────


def _write(tmp_path: Path, content: str, name: str = "vkospi.csv") -> Path:
    p = tmp_path / name
    p.write_text(content, encoding="utf-8")
    return p


class TestParseCsvAcceptedFormats:
    def test_iso_date_with_header(self, tmp_path: Path) -> None:
        p = _write(tmp_path, "date,close\n2024-01-02,18.42\n2024-01-03,17.95\n")
        bars = parse_csv(p)
        assert len(bars) == 2
        assert bars[0].bar_date == date(2024, 1, 2)
        assert bars[0].close == pytest.approx(18.42)
        assert bars[1].close == pytest.approx(17.95)

    def test_yyyymmdd_date_no_header(self, tmp_path: Path) -> None:
        p = _write(tmp_path, "20240102,18.42\n20240103,17.95\n")
        bars = parse_csv(p)
        assert bars[0].bar_date == date(2024, 1, 2)

    def test_dot_date_format(self, tmp_path: Path) -> None:
        p = _write(tmp_path, "2024.01.02,18.42\n")
        bars = parse_csv(p)
        assert bars[0].bar_date == date(2024, 1, 2)

    def test_korean_header_tokens(self, tmp_path: Path) -> None:
        p = _write(tmp_path, "일자,종가\n2024-01-02,18.42\n")
        bars = parse_csv(p)
        assert len(bars) == 1
        assert bars[0].close == pytest.approx(18.42)

    def test_reversed_column_order_via_header(self, tmp_path: Path) -> None:
        # Header puts close first, date second — provider must respect order.
        p = _write(tmp_path, "close,date\n18.42,2024-01-02\n")
        bars = parse_csv(p)
        assert bars[0].bar_date == date(2024, 1, 2)
        assert bars[0].close == pytest.approx(18.42)

    def test_thousands_separator_in_close(self, tmp_path: Path) -> None:
        # Some KRX exports include comma thousands separators.
        p = _write(tmp_path, "date,close\n2024-01-02,1,842.50\n")
        # csv.reader splits on comma so "1,842.50" arrives as two cells.
        # The current parser cannot reconstruct mid-cell commas without
        # quoting; verify we either parse the quoted form correctly.
        p.write_text(
            'date,close\n2024-01-02,"1,842.50"\n',
            encoding="utf-8",
        )
        bars = parse_csv(p)
        assert bars[0].close == pytest.approx(1842.50)

    def test_skips_blank_and_comment_lines(self, tmp_path: Path) -> None:
        p = _write(
            tmp_path,
            "# KRX export 2026-04-30\n"
            "date,close\n"
            "\n"
            "2024-01-02,18.42\n"
            "# mid-file comment\n"
            "2024-01-03,17.95\n",
        )
        bars = parse_csv(p)
        assert len(bars) == 2

    def test_dedupes_duplicate_dates_keeping_last(self, tmp_path: Path) -> None:
        # If a CSV has the same date twice (e.g. operator concatenated
        # exports), the later row wins via dict reassignment.
        p = _write(
            tmp_path,
            "date,close\n2024-01-02,18.42\n2024-01-02,99.99\n",
        )
        bars = parse_csv(p)
        assert len(bars) == 1
        assert bars[0].close == pytest.approx(99.99)

    def test_sorted_output(self, tmp_path: Path) -> None:
        # Provider must sort regardless of source order.
        p = _write(
            tmp_path,
            "date,close\n2024-01-05,20.0\n2024-01-02,18.42\n2024-01-03,17.95\n",
        )
        bars = parse_csv(p)
        assert [b.bar_date for b in bars] == [
            date(2024, 1, 2), date(2024, 1, 3), date(2024, 1, 5),
        ]


# ── parse_csv — rejection paths ──────────────────────────────────────────


class TestParseCsvRejectionPaths:
    def test_missing_file_raises(self, tmp_path: Path) -> None:
        with pytest.raises(VkospiDataError, match="not found"):
            parse_csv(tmp_path / "nope.csv")

    def test_empty_file_raises(self, tmp_path: Path) -> None:
        p = _write(tmp_path, "")
        with pytest.raises(VkospiDataError, match="empty"):
            parse_csv(p)

    def test_only_comments_raises(self, tmp_path: Path) -> None:
        p = _write(tmp_path, "# this is a header-only file\n# nothing else\n")
        with pytest.raises(VkospiDataError, match="empty"):
            parse_csv(p)

    def test_all_rows_unparseable_raises(self, tmp_path: Path) -> None:
        p = _write(
            tmp_path,
            "garbage,values\n??,??\nnotadate,notafloat\n",
        )
        with pytest.raises(VkospiDataError, match="zero usable"):
            parse_csv(p)

    def test_negative_close_rejected(self, tmp_path: Path) -> None:
        # Negative VKOSPI close is impossible — silently skipped.
        p = _write(
            tmp_path,
            "date,close\n2024-01-02,-1.0\n2024-01-03,18.0\n",
        )
        bars = parse_csv(p)
        assert len(bars) == 1
        assert bars[0].bar_date == date(2024, 1, 3)


# ── make_csv_provider — async slicing ────────────────────────────────────


class TestMakeCsvProvider:
    @pytest.mark.asyncio
    async def test_slices_to_window(self, tmp_path: Path) -> None:
        p = _write(
            tmp_path,
            "date,close\n2024-01-02,18.0\n2024-01-03,18.5\n2024-01-04,19.0\n",
        )
        provider = make_csv_provider(p)
        bars = await provider(date(2024, 1, 3), date(2024, 1, 4))
        assert len(bars) == 2
        assert bars[0].bar_date == date(2024, 1, 3)

    @pytest.mark.asyncio
    async def test_caches_parse_across_calls(self, tmp_path: Path) -> None:
        p = _write(tmp_path, "date,close\n2024-01-02,18.0\n")
        provider = make_csv_provider(p)
        a = await provider(date(2024, 1, 1), date(2024, 12, 31))
        # Mutate the file underneath; the provider should not re-parse.
        p.write_text("date,close\n2024-01-02,99.0\n", encoding="utf-8")
        b = await provider(date(2024, 1, 1), date(2024, 12, 31))
        assert a == b
        assert a[0].close == pytest.approx(18.0)

    @pytest.mark.asyncio
    async def test_empty_window_returns_empty(self, tmp_path: Path) -> None:
        p = _write(tmp_path, "date,close\n2024-01-02,18.0\n")
        provider = make_csv_provider(p)
        bars = await provider(date(2025, 1, 1), date(2025, 12, 31))
        assert bars == ()


# ── attach_csv_provider — wires VkospiClient ─────────────────────────────


class TestAttachCsvProvider:
    @pytest.mark.asyncio
    async def test_client_uses_csv_after_attach(self, tmp_path: Path) -> None:
        p = _write(
            tmp_path,
            "date,close\n2024-01-02,18.0\n2024-01-03,18.5\n2024-01-04,19.0\n",
        )
        client = VkospiClient()
        attach_csv_provider(client, p)
        bars = await client.get_history(
            start=date(2024, 1, 2), end=date(2024, 1, 4),
        )
        assert len(bars) == 3

    @pytest.mark.asyncio
    async def test_client_get_delta_uses_csv(self, tmp_path: Path) -> None:
        p = _write(
            tmp_path,
            "date,close\n2024-01-02,18.0\n2024-01-03,22.0\n",
        )
        client = VkospiClient()
        attach_csv_provider(client, p)
        d = await client.get_delta_at(date(2024, 1, 3))
        assert d.fear_regime is True
        assert d.pct_change == pytest.approx((22.0 - 18.0) / 18.0, abs=1e-9)
