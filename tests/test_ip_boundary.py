from __future__ import annotations

import subprocess
from pathlib import Path

import pytest


# IP boundary regression guards.
# After the v2.0.0 IP cleanup (12 v1.x versions yanked, dca_sizing keystone
# removed, sibling-project attribution stripped), these tests fail any
# future commit that reintroduces forbidden text into the public source
# distribution.

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SCANNED_DIRS = ("src", "docs", "configs", "scripts", "tests")
_SCANNED_EXTS = ("*.py", "*.md", "*.yaml", "*.toml", "*.sh", "*.txt")


def _grep(pattern: str) -> tuple[int, str]:
    cmd = [
        "grep", "-rln", pattern,
        *[f"--include={ext}" for ext in _SCANNED_EXTS],
        *[str(_REPO_ROOT / d) for d in _SCANNED_DIRS if (_REPO_ROOT / d).exists()],
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    return result.returncode, result.stdout


# WHY: forbidden tokens are sibling internal projects + filesystem paths
# that would identify them. Self-references (this test file) are excluded
# via _exclude_self.

_FORBIDDEN_PROJECT_NAMES = (
    "TITAN",
    "MOET",
    "BETASTRIKE",
)

_FORBIDDEN_PATHS = (
    "/Applications/TITAN",
    "/Applications/MOET",
    "/Applications/BETASTRIKE",
    "/Applications/ATLAS",
)


def _exclude_self(stdout: str) -> list[str]:
    self_name = Path(__file__).name
    return [
        line for line in stdout.splitlines()
        if line and self_name not in line
    ]


@pytest.mark.parametrize("token", _FORBIDDEN_PROJECT_NAMES)
def test_no_forbidden_project_name(token: str) -> None:
    _, stdout = _grep(token)
    hits = _exclude_self(stdout)
    assert not hits, (
        f"IP boundary breach: forbidden project name {token!r} found in:\n"
        + "\n".join(hits)
    )


@pytest.mark.parametrize("path", _FORBIDDEN_PATHS)
def test_no_forbidden_filesystem_path(path: str) -> None:
    _, stdout = _grep(path)
    hits = _exclude_self(stdout)
    assert not hits, (
        f"IP boundary breach: forbidden filesystem path {path!r} found in:\n"
        + "\n".join(hits)
    )


def test_no_dca_sizing_imports_or_refs() -> None:
    # WHY: dca_sizing was the v1.4 sizing-tier keystone removed in v2.0.0
    # (INV-GS-111 deprecated). Guard against reintroduction.
    _, stdout = _grep("dca_sizing")
    hits = _exclude_self(stdout)
    # Roadmap + PRD + README are allowed to reference the v2.0 removal
    # historically (migration notes pointing former v1.x users to the change).
    allowed_suffixes = (
        "docs/ROADMAP_v2.md",
        "docs/v2.1_PRD.md",
        "README.md",
    )
    hits = [h for h in hits if not any(h.endswith(s) for s in allowed_suffixes)]
    assert not hits, (
        "dca_sizing reintroduced after v2.0 deletion:\n" + "\n".join(hits)
    )


def test_prediction_dataclass_has_no_dca_sizing_field() -> None:
    # WHY: INV-GS-111 deprecated v2.0. Verify the field is gone at the
    # type system level, not just absent from docs.
    import dataclasses

    from glostat.predictor.types import Prediction

    field_names = {f.name for f in dataclasses.fields(Prediction)}
    assert "dca_sizing" not in field_names, (
        f"INV-GS-111 (deprecated v2.0): Prediction must not carry "
        f"a dca_sizing field. Current fields: {sorted(field_names)}"
    )


def test_inv_gs_111_marked_deprecated_in_yaml() -> None:
    import yaml

    data = yaml.safe_load((_REPO_ROOT / "configs" / "invariants.yaml").read_text())
    inv = data["invariants"]["INV-GS-111"]
    assert inv.get("deprecated") is True or inv.get("active_in") == [], (
        f"INV-GS-111 must be marked deprecated in v2.0 — got {inv}"
    )
