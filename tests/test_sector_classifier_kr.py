from __future__ import annotations

import pytest

from glostat.data.sector_classifier_kr import (
    CycleClass,
    KrSector,
    cycle_class_of,
    cyclical_universe,
    info_for,
    is_cyclical,
    is_refining,
    refining_universe,
    sector_of,
)

# v1.5 P6 — sector classifier tests.


class TestSectorOf:
    def test_sk_innovation_is_refining(self) -> None:
        assert sector_of("096770") == KrSector.REFINING

    def test_s_oil_is_refining(self) -> None:
        assert sector_of("010950") == KrSector.REFINING

    def test_posco_is_steel(self) -> None:
        assert sector_of("005490") == KrSector.STEEL

    def test_lg_chem_is_chemicals(self) -> None:
        assert sector_of("051910") == KrSector.CHEMICALS

    def test_hmm_is_shipping(self) -> None:
        assert sector_of("011200") == KrSector.SHIPPING

    def test_hyundai_construction_is_construction(self) -> None:
        assert sector_of("000720") == KrSector.CONSTRUCTION

    def test_hyundai_motor_is_consumer_cycl(self) -> None:
        assert sector_of("005380") == KrSector.CONSUMER_CYCL

    def test_samsung_electronics_is_semiconductor(self) -> None:
        assert sector_of("005930") == KrSector.SEMICONDUCTOR

    def test_naver_is_internet(self) -> None:
        assert sector_of("035420") == KrSector.INTERNET

    def test_kbfg_is_bank(self) -> None:
        assert sector_of("105560") == KrSector.BANK

    def test_unknown_returns_other(self) -> None:
        assert sector_of("999999") == KrSector.OTHER

    def test_with_ks_suffix_normalizes(self) -> None:
        assert sector_of("096770.KS") == KrSector.REFINING


class TestCycleClassOf:
    def test_refining_is_cyclical(self) -> None:
        assert cycle_class_of("096770") == CycleClass.CYCLICAL

    def test_bank_is_defensive(self) -> None:
        assert cycle_class_of("105560") == CycleClass.DEFENSIVE

    def test_internet_is_growth(self) -> None:
        assert cycle_class_of("035420") == CycleClass.GROWTH

    def test_unknown_is_other(self) -> None:
        assert cycle_class_of("999999") == CycleClass.OTHER


class TestIsCyclical:
    @pytest.mark.parametrize("ticker", [
        "096770",  # SK이노베이션 (refining)
        "010950",  # S-Oil
        "005490",  # POSCO홀딩스
        "051910",  # LG화학
        "011200",  # HMM (shipping)
        "000720",  # 현대건설
        "005380",  # 현대차
    ])
    def test_known_cyclicals(self, ticker: str) -> None:
        assert is_cyclical(ticker) is True

    @pytest.mark.parametrize("ticker", [
        "005930",  # 삼성전자 (semiconductor — growth, not cyclical)
        "035420",  # NAVER
        "105560",  # KB금융
        "017670",  # SK텔레콤
        "999999",  # unknown
    ])
    def test_known_non_cyclicals(self, ticker: str) -> None:
        assert is_cyclical(ticker) is False


class TestIsRefining:
    def test_sk_innovation_yes(self) -> None:
        assert is_refining("096770") is True

    def test_s_oil_yes(self) -> None:
        assert is_refining("010950") is True

    def test_lg_chem_no(self) -> None:
        # LG화학은 chemicals, not refining
        assert is_refining("051910") is False

    def test_samsung_no(self) -> None:
        assert is_refining("005930") is False


class TestInfoFor:
    def test_sk_innovation_info(self) -> None:
        info = info_for("096770")
        assert info.ticker == "096770"
        assert info.sector == KrSector.REFINING
        assert info.cycle_class == CycleClass.CYCLICAL
        assert info.is_cyclical is True


class TestCyclicalUniverse:
    def test_includes_known_refiners(self) -> None:
        universe = cyclical_universe()
        assert "096770" in universe   # SK이노베이션
        assert "010950" in universe   # S-Oil
        assert "005490" in universe   # POSCO

    def test_excludes_growth(self) -> None:
        universe = cyclical_universe()
        assert "005930" not in universe   # 삼성전자
        assert "035420" not in universe   # NAVER

    def test_sorted(self) -> None:
        universe = cyclical_universe()
        assert list(universe) == sorted(universe)


class TestRefiningUniverse:
    def test_only_refiners(self) -> None:
        universe = refining_universe()
        for t in universe:
            assert sector_of(t) == KrSector.REFINING

    def test_includes_sk_and_s_oil(self) -> None:
        universe = refining_universe()
        assert "096770" in universe
        assert "010950" in universe

    def test_excludes_chemicals(self) -> None:
        # 롯데케미칼 (011170) is CHEMICALS, not REFINING.
        universe = refining_universe()
        assert "011170" not in universe
