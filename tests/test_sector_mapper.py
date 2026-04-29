from __future__ import annotations

import pytest

from glostat.data.sector_mapper import (
    GICS_SECTORS,
    UNKNOWN_SECTOR,
    get_sector,
    sic_to_gics,
)

# Sprint 1 PR #4 — SIC → GICS sector mapper tests.


# ── Direct SIC → GICS mappings ─────────────────────────────────────────────


def test_sic_to_gics_technology_software() -> None:
    # 7372 = prepackaged software → Technology
    assert sic_to_gics("7372") == "Technology"
    assert sic_to_gics(7372) == "Technology"


def test_sic_to_gics_technology_hardware() -> None:
    # 3571 = electronic computers → Technology
    assert sic_to_gics("3571") == "Technology"


def test_sic_to_gics_healthcare() -> None:
    # 8000-8099 = health services
    assert sic_to_gics("8000") == "Healthcare"
    assert sic_to_gics("8099") == "Healthcare"
    # 2834 = pharmaceuticals
    assert sic_to_gics("2834") == "Healthcare"


def test_sic_to_gics_financials_banks() -> None:
    assert sic_to_gics("6020") == "Financials"
    assert sic_to_gics("6199") == "Financials"


def test_sic_to_gics_financials_insurance() -> None:
    assert sic_to_gics("6311") == "Financials"


def test_sic_to_gics_real_estate() -> None:
    assert sic_to_gics("6500") == "RealEstate"
    assert sic_to_gics("6552") == "RealEstate"


def test_sic_to_gics_energy() -> None:
    assert sic_to_gics("1311") == "Energy"  # crude oil & natgas
    assert sic_to_gics("2911") == "Energy"  # petroleum refining


def test_sic_to_gics_consumer_staples() -> None:
    assert sic_to_gics("2080") == "ConsumerStaples"
    assert sic_to_gics("5411") == "ConsumerStaples"  # grocery stores


def test_sic_to_gics_consumer_discretionary() -> None:
    assert sic_to_gics("5651") == "ConsumerDiscretionary"  # apparel stores
    assert sic_to_gics("7011") == "ConsumerDiscretionary"  # hotels/motels


def test_sic_to_gics_industrials() -> None:
    assert sic_to_gics("3711") == "ConsumerDiscretionary"  # motor vehicles overlap
    assert sic_to_gics("3559") == "Industrials"  # special industry machinery
    assert sic_to_gics("4011") == "Industrials"  # railroads


def test_sic_to_gics_utilities() -> None:
    assert sic_to_gics("4911") == "Utilities"
    assert sic_to_gics("4931") == "Utilities"


def test_sic_to_gics_communications() -> None:
    assert sic_to_gics("4813") == "Communications"  # telephone communications
    assert sic_to_gics("4833") == "Communications"  # broadcasting


def test_sic_to_gics_materials_chemicals() -> None:
    assert sic_to_gics("2812") == "Materials"  # alkalies and chlorine


def test_sic_to_gics_unknown_returns_other() -> None:
    assert sic_to_gics("9999") == "OTHER"  # nonclassifiable


def test_sic_to_gics_none_returns_unknown() -> None:
    assert sic_to_gics(None) == UNKNOWN_SECTOR


def test_sic_to_gics_empty_string_returns_unknown() -> None:
    assert sic_to_gics("") == UNKNOWN_SECTOR
    assert sic_to_gics("   ") == UNKNOWN_SECTOR


def test_sic_to_gics_invalid_returns_unknown() -> None:
    assert sic_to_gics("abc") == UNKNOWN_SECTOR
    assert sic_to_gics("0") == UNKNOWN_SECTOR
    assert sic_to_gics("-100") == UNKNOWN_SECTOR


def test_gics_sectors_constant_complete() -> None:
    assert "Technology" in GICS_SECTORS
    assert "Healthcare" in GICS_SECTORS
    assert "Financials" in GICS_SECTORS
    assert "OTHER" in GICS_SECTORS
    # 11 GICS + OTHER bucket
    assert len(GICS_SECTORS) >= 11


# ── get_sector via injected resolver ───────────────────────────────────────


@pytest.mark.asyncio
async def test_get_sector_via_resolver() -> None:
    async def resolver(_t: str) -> str:
        return "7372"

    sector = await get_sector("AAPL", resolve_sic=resolver)
    assert sector == "Technology"


@pytest.mark.asyncio
async def test_get_sector_resolver_returns_none() -> None:
    async def resolver(_t: str) -> str | None:
        return None

    sector = await get_sector("AAPL", resolve_sic=resolver)
    assert sector == UNKNOWN_SECTOR


@pytest.mark.asyncio
async def test_get_sector_resolver_raises() -> None:
    async def resolver(_t: str) -> str:
        raise RuntimeError("network down")

    sector = await get_sector("AAPL", resolve_sic=resolver)
    assert sector == UNKNOWN_SECTOR  # WHY: graceful degradation; never crash caller
