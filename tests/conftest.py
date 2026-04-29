from __future__ import annotations

import os

import pytest

# Skip network-marked tests unless NETWORK_TESTS=1.
# Keeps CI offline; opt-in for live data plane verification.


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if os.environ.get("NETWORK_TESTS") == "1":
        return
    skip_network = pytest.mark.skip(reason="network test — set NETWORK_TESTS=1 to enable")
    for item in items:
        if "network" in item.keywords:
            item.add_marker(skip_network)
