from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

from glostat.core.errors import ExpertSkipError
from glostat.data.data_router import DataRouter
from glostat.data.naver_kr_client import KrFlowBar
from glostat.experts.e_foreign_reversal import EForeignReversalExpert
from glostat.predictor.kr_universe import (
    KOSPI200_UNIVERSE,
    is_kospi200,
    is_kr_ticker,
    kr_canonical,
    load_kospi200,
)

# ── universe loader ───────────────────────────────────────────────────────


def test_load_kospi200_returns_200_tickers() -> None:
    universe = load_kospi200()
    assert len(universe) == 200


def test_load_kospi200_includes_known_megacaps() -> None:
    universe = load_kospi200()
    # Known KOSPI 200 megacaps: 005930 (Samsung), 000660 (SK Hynix),
    # 096770 (SK Innovation — the K1 motivation), 051910 (LG Chem).
    expected = {"005930", "000660", "096770", "051910", "035420", "035720"}
    missing = expected - universe
    assert missing == set(), f"missing megacaps: {missing}"


def test_load_kospi200_returns_empty_when_file_missing(tmp_path: Path) -> None:
    universe = load_kospi200(path=tmp_path / "does_not_exist.txt")
    assert universe == frozenset()


def test_load_kospi200_skips_comments_and_blank_lines(tmp_path: Path) -> None:
    p = tmp_path / "tiny.txt"
    p.write_text("# header\n005930\n\n# another\n000660\n")
    assert load_kospi200(path=p) == frozenset({"005930", "000660"})


def test_load_kospi200_only_accepts_six_digit_codes(tmp_path: Path) -> None:
    p = tmp_path / "noisy.txt"
    p.write_text("005930\nAAPL\n12345\n000660\n")
    assert load_kospi200(path=p) == frozenset({"005930", "000660"})


# ── module-level cache ────────────────────────────────────────────────────


def test_module_level_universe_loaded() -> None:
    assert "005930" in KOSPI200_UNIVERSE
    assert "096770" in KOSPI200_UNIVERSE


def test_is_kr_ticker_helper() -> None:
    assert is_kr_ticker("005930") is True
    assert is_kr_ticker("096770.KS") is True
    assert is_kr_ticker("AAPL") is False


def test_kr_canonical_strips_suffix() -> None:
    assert kr_canonical("005930.KS") == "005930"
    assert kr_canonical("096770") == "096770"


def test_is_kospi200_membership() -> None:
    # SK Innovation (the K1 motivating bug — must be IN the universe).
    assert is_kospi200("096770") is True
    assert is_kospi200("096770.KS") is True
    # An obvious non-member.
    assert is_kospi200("999999") is False


# ── EForeignReversalExpert universe gate ──────────────────────────────────


class _StubNaver:
    def __init__(self, bars: list[KrFlowBar]) -> None:
        self._bars = bars

    def load_cached(self, code: str) -> list[KrFlowBar]:
        return []

    async def fetch_history(self, code: str, *, max_pages: int = 6) -> list[KrFlowBar]:
        return self._bars

    def save_cache(self, code: str, bars: list[KrFlowBar]) -> Path:
        return Path("/dev/null")


def _bar(code: str, idx: int, fnet: float, organ: float = 0.0) -> KrFlowBar:
    base = date(2026, 4, 1)
    return KrFlowBar(
        code=code, bar_date=base + timedelta(days=idx),
        close_price=10000.0 + idx,
        organ_net=organ, foreign_net=fnet,
        foreign_holdings=0, foreign_hold_pct=0.0,
    )


def _make_router(naver: _StubNaver) -> DataRouter:
    r = DataRouter()
    r.register_client("naver_kr", naver)
    return r


@pytest.mark.asyncio
async def test_expert_compute_skip_when_not_in_universe() -> None:
    expert = EForeignReversalExpert(
        router=_make_router(_StubNaver([])),
        kospi200=frozenset({"005930"}),
    )
    with pytest.raises(ExpertSkipError) as exc:
        await expert.compute("999999", datetime.now(tz=UTC))
    assert "not in KOSPI 200" in str(exc.value)


@pytest.mark.asyncio
async def test_expert_compute_skip_when_too_few_bars() -> None:
    expert = EForeignReversalExpert(
        router=_make_router(_StubNaver([_bar("005930", i, 0.0) for i in range(2)])),
        kospi200=frozenset({"005930"}),
    )
    with pytest.raises(ExpertSkipError) as exc:
        await expert.compute("005930", datetime.now(tz=UTC))
    assert "insufficient" in str(exc.value).lower()


@pytest.mark.asyncio
async def test_expert_compute_returns_signal_for_reversal_pattern() -> None:
    # 4 consecutive sells then buy → REVERSAL_BUY
    bars = [
        _bar("005930", 0, -100.0),
        _bar("005930", 1, -100.0),
        _bar("005930", 2, -100.0),
        _bar("005930", 3, -100.0),
        _bar("005930", 4, +100.0, organ=+50.0),
    ]
    expert = EForeignReversalExpert(
        router=_make_router(_StubNaver(bars)),
        kospi200=frozenset({"005930"}),
    )
    sig = await expert.compute("005930", datetime.now(tz=UTC))
    assert sig.expert_name == "E_FOREIGN_REVERSAL"
    assert sig.direction == "LONG"
    assert sig.net_score > 0
    assert sig.metadata
