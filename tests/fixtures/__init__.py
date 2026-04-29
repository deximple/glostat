from __future__ import annotations

import json
from pathlib import Path
from typing import Any

_FIXTURES_DIR = Path(__file__).resolve().parent


def load_fixture(name: str) -> dict[str, Any]:
    path = _FIXTURES_DIR / name
    return json.loads(path.read_text("utf-8"))
