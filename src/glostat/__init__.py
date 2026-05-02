from __future__ import annotations

__version__ = "1.6.2"
__plan_version__ = "v1.6"
__sprint__ = 0
__status__ = (
    "v1.6.2 — Option A wave 2: cyclical + commodity hindcast wired. "
    "kr-hindcast now produces real calibration for E_FUNDAMENTAL_KR_CYCLICAL "
    "(EV/EBITDA + commodity-cycle point-in-time) and E_COMMODITY_INDEX_KR "
    "(WTI + crack-spread momentum, refining-only). commodity_client gained "
    "point-in-time slicing so cache stays single-fetch per commodity per run."
)

__all__ = ["__plan_version__", "__sprint__", "__status__", "__version__"]
