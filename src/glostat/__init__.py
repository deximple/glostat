from __future__ import annotations

__version__ = "1.6.0"
__plan_version__ = "v1.6"
__sprint__ = 0
__status__ = (
    "v1.6 — Event-aware (P5 Event-Driven panel absorption): "
    "kr_calendar_client (DART earnings + BoK 금통위 + auto-scrape OPEC) + "
    "E_PEAD_KR (KR post-earnings drift T+5..T+30) + composite CI calendar "
    "widening (D-day < 7 → ×1.5σ, < 3 → ×2.0σ) + next_triggers populated "
    "with concrete D-day countdowns"
)

__all__ = ["__plan_version__", "__sprint__", "__status__", "__version__"]
