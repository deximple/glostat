from __future__ import annotations

import pytest

from glostat.core.errors import ConfigError
from glostat.data.data_router import DataRouter

# ── Helpers ────────────────────────────────────────────────────────────────


class _Stub:
    def __init__(self, label: str) -> None:
        self.label = label

    def __repr__(self) -> str:  # pragma: no cover
        return f"_Stub({self.label})"


@pytest.fixture
def router() -> DataRouter:
    r = DataRouter()
    r.register_client("yfinance", _Stub("yf"))
    r.register_client("sec_edgar", _Stub("sec"))
    r.register_client("bigdata", _Stub("bd"))
    r.register_client("fred", _Stub("fred"))
    return r


# ── Phase resolution ───────────────────────────────────────────────────────


def test_active_phase_defaults_to_mvp(
    router: DataRouter, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("GLOSTAT_PHASE", raising=False)
    # Point at non-existent yaml so file fallback also yields "mvp".
    router.budget_yaml = router.budget_yaml.with_name("does_not_exist.yaml")
    assert router.active_phase() == "mvp"


def test_active_phase_env_override(
    router: DataRouter, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GLOSTAT_PHASE", "phase_2")
    assert router.active_phase() == "phase_2"


def test_active_phase_invalid_env_raises(
    router: DataRouter, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GLOSTAT_PHASE", "phase_42")
    with pytest.raises(ConfigError, match="INV-GS-039"):
        router.active_phase()


# ── INV-GS-039: MVP routing ────────────────────────────────────────────────


def test_route_e_fundamental_ohlcv_returns_yfinance_in_mvp(
    router: DataRouter, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GLOSTAT_PHASE", "mvp")
    client, method = router.route("E_FUNDAMENTAL", "ohlcv")
    assert client.label == "yf"
    assert method == "get_ohlcv"


def test_route_e_fundamental_filings_returns_sec_in_mvp(
    router: DataRouter, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GLOSTAT_PHASE", "mvp")
    client, method = router.route("E_FUNDAMENTAL", "filings")
    assert client.label == "sec"
    assert method == "get_filings"


def test_route_e_fund_flow_13f_returns_sec_in_mvp(
    router: DataRouter, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GLOSTAT_PHASE", "mvp")
    client, method = router.route("E_FUND_FLOW", "13f")
    assert client.label == "sec"
    assert method == "get_13f_holdings"


# ── INV-GS-036/039: Bigdata blocked in MVP ─────────────────────────────────


def test_route_e_narrative_search_blocked_in_mvp(
    router: DataRouter, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GLOSTAT_PHASE", "mvp")
    with pytest.raises(ConfigError, match=r"INV-GS-036|MVP"):
        router.route("E_NARRATIVE", "search")


def test_route_e_cascade_filings_blocked_in_mvp(
    router: DataRouter, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GLOSTAT_PHASE", "mvp")
    with pytest.raises(ConfigError, match=r"MVP|INV-GS"):
        router.route("E_CASCADE", "filings")


# ── INV-GS-040: Phase 2 needs explicit consent for bigdata ─────────────────


def test_route_phase_2_without_consent_raises(
    router: DataRouter, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GLOSTAT_PHASE", "phase_2")
    with pytest.raises(ConfigError, match="INV-GS-040"):
        router.route("E_NARRATIVE", "search")


def test_route_phase_2_with_consent_allows_bigdata(
    router: DataRouter, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GLOSTAT_PHASE", "phase_2")
    router.grant_consent("phase_2")
    client, method = router.route("E_NARRATIVE", "search")
    assert client.label == "bd"
    assert method == "bigdata_search"


def test_route_phase_2_prefers_free_when_available(
    router: DataRouter, monkeypatch: pytest.MonkeyPatch
) -> None:
    # E_FUNDAMENTAL fundamentals: yfinance (mvp) appears before bigdata (phase_2)
    monkeypatch.setenv("GLOSTAT_PHASE", "phase_2")
    router.grant_consent("phase_2")
    client, method = router.route("E_FUNDAMENTAL", "fundamentals")
    assert client.label == "yf"
    assert method == "get_fundamentals"


# ── INV-GS-039: unknown route → ConfigError ────────────────────────────────


def test_route_unknown_pair_raises(
    router: DataRouter, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GLOSTAT_PHASE", "mvp")
    with pytest.raises(ConfigError, match="INV-GS-039"):
        router.route("E_FUNDAMENTAL", "moon_phase")


def test_route_missing_client_registration_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GLOSTAT_PHASE", "mvp")
    r = DataRouter()  # no clients registered
    with pytest.raises(ConfigError, match="no client registered"):
        r.route("E_FUNDAMENTAL", "ohlcv")


# ── Phase 3 cascade routing ────────────────────────────────────────────────


def test_route_phase_3_cascade_filings_with_consent(
    router: DataRouter, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GLOSTAT_PHASE", "phase_3")
    router.grant_consent("phase_3")
    client, method = router.route("E_CASCADE", "filings")
    assert client.label == "bd"
    assert method == "bigdata_search"


def test_revoke_consent_blocks_again(
    router: DataRouter, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GLOSTAT_PHASE", "phase_2")
    router.grant_consent("phase_2")
    router.route("E_NARRATIVE", "search")  # OK
    router.revoke_consent("phase_2")
    with pytest.raises(ConfigError, match="INV-GS-040"):
        router.route("E_NARRATIVE", "search")


# ── Sprint 1 PR #3: E_FUND_FLOW routing additions ──────────────────────────


def test_route_e_fund_flow_institutional_holders(
    router: DataRouter, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GLOSTAT_PHASE", "mvp")
    client, method = router.route("E_FUND_FLOW", "institutional_holders")
    assert client.label == "yf"
    assert method == "get_holders"


def test_route_e_fund_flow_13f_quarterly(
    router: DataRouter, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GLOSTAT_PHASE", "mvp")
    client, method = router.route("E_FUND_FLOW", "13f_quarterly")
    assert client.label == "sec"
    assert method == "get_filings"


def test_route_e_fund_flow_13f_holdings(
    router: DataRouter, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GLOSTAT_PHASE", "mvp")
    client, method = router.route("E_FUND_FLOW", "13f_holdings")
    assert client.label == "sec"
    assert method == "get_13f_holdings"


def test_route_e_fund_flow_holders_alias(
    router: DataRouter, monkeypatch: pytest.MonkeyPatch
) -> None:
    # holders alias still works for backwards compat with existing routes.
    monkeypatch.setenv("GLOSTAT_PHASE", "mvp")
    client, method = router.route("E_FUND_FLOW", "holders")
    assert client.label == "yf"
    assert method == "get_holders"
