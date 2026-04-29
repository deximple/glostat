from __future__ import annotations

import argparse
import asyncio
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

from glostat.cli_mocks import MockSecEdgarClient, MockYFinanceClient
from glostat.cli_predict_print import print_prediction
from glostat.data.data_router import DataRouter
from glostat.data.sec_edgar_client import SecEdgarClient
from glostat.data.snapshot_broker import SnapshotBroker
from glostat.data.yfinance_client import YFinanceClient
from glostat.experts import EFundamentalExpert, EFundFlowExpert, ETimeExpert
from glostat.predictor.calibration import (
    CalibrationTable,
    load_calibration,
    synthetic_calibration_for_mock,
)
from glostat.predictor.composite import predict
from glostat.predictor.thesis_wrappers import collect_contributions
from glostat.predictor.types import (
    Horizon,
    Prediction,
    prediction_to_canonical_json,
)
from glostat.risk.compliance_gate import ComplianceContext, assert_personal_use

# v1.0 predict + calibrate subcommands. Lifted out of cli.py to keep that file
# under the 400-line house rule while leaving the legacy verdict surface intact.

_DEFAULT_SNAPSHOT_ROOT: Final[Path] = Path("cache") / "snapshots"
_BUDGET_YAML: Final[Path] = (
    Path(__file__).resolve().parents[2] / "configs" / "budget.yaml"
)
_FIXTURES_DIR: Final[Path] = (
    Path(__file__).resolve().parents[2] / "tests" / "fixtures"
)


def add_predict_subparser(sub: Any) -> None:
    p = sub.add_parser(
        "predict",
        help="Issue a Prediction (probability + evidence) for a ticker.",
    )
    p.add_argument("ticker")
    p.add_argument("--mock", action="store_true",
                   help="Use bundled fixtures instead of network calls.")
    p.add_argument("--horizon", default="swing_30d",
                   choices=["intraday", "swing_5d", "swing_30d", "long_3y"],
                   help="Prediction horizon. Default swing_30d.")
    p.add_argument("--jurisdiction", default="US",
                   choices=["KR", "US", "EU", "JP", "TW", "HK", "DEFAULT"],
                   help="Compliance disclaimer jurisdiction. Default US.")
    p.add_argument("--json", action="store_true",
                   help="Emit prediction as canonical JSON (machine-readable).")


def add_calibrate_subparser(sub: Any) -> None:
    p = sub.add_parser(
        "calibrate",
        help="Refresh calibration_table.parquet from cached hindcast reports.",
    )
    p.add_argument("--mock", action="store_true",
                   help="Use synthetic calibration (no cache reads).")
    p.add_argument("--out", type=Path, default=None,
                   help="Output parquet path (default cache/calibration_table.parquet).")


def cmd_predict(args: argparse.Namespace) -> int:
    ts = datetime.now(tz=UTC)
    ctx = ComplianceContext(
        user_profile_hash="0" * 64,
        jurisdiction=args.jurisdiction,
    )
    assert_personal_use(ctx)

    broker = SnapshotBroker(root=_DEFAULT_SNAPSHOT_ROOT)
    cal_table = (
        synthetic_calibration_for_mock() if args.mock else load_calibration()
    )
    try:
        if args.mock:
            prediction = asyncio.run(
                _predict_mock(args.ticker, args.horizon, ts, broker, cal_table)
            )
        else:
            prediction = asyncio.run(
                _predict_live(args.ticker, args.horizon, ts, broker, cal_table)
            )
    finally:
        broker.close()

    if args.json:
        print(prediction_to_canonical_json(prediction))
    else:
        print_prediction(prediction)
    return 0


async def _predict_live(
    ticker: str,
    horizon: Horizon,
    ts: datetime,
    broker: SnapshotBroker,
    cal_table: CalibrationTable,
) -> Prediction:
    yf_client = YFinanceClient(snapshot_broker=broker)
    sec_user_agent = os.environ.get("GLOSTAT_SEC_USER_AGENT")
    sec_client = SecEdgarClient(user_agent=sec_user_agent, snapshot_broker=broker)
    router = DataRouter(budget_yaml=_BUDGET_YAML)
    router.register_client("yfinance", yf_client)
    router.register_client("sec_edgar", sec_client)
    fundamental = EFundamentalExpert(router=router)
    time_expert = ETimeExpert(router=router)
    fund_flow = EFundFlowExpert(router=router)
    try:
        contribs = await collect_contributions(
            ticker=ticker, ts=ts, cal_table=cal_table,
            fundamental_expert=fundamental,
            time_expert=time_expert,
            fund_flow_expert=fund_flow,
        )
    finally:
        await sec_client.aclose()
    return predict(
        ticker=ticker, horizon=horizon, contributions=contribs,
        cal_table=cal_table, issued_at=ts,
    )


async def _predict_mock(
    ticker: str,
    horizon: Horizon,
    ts: datetime,
    broker: SnapshotBroker,
    cal_table: CalibrationTable,
) -> Prediction:
    fixture = _load_fixture(ticker)
    yf_client = MockYFinanceClient(broker=broker, fixture=fixture)
    sec_client = MockSecEdgarClient(broker=broker, fixture=fixture)
    router = DataRouter(budget_yaml=_BUDGET_YAML)
    router.register_client("yfinance", yf_client)
    router.register_client("sec_edgar", sec_client)
    fundamental = EFundamentalExpert(router=router)
    time_expert = ETimeExpert(router=router)
    fund_flow = EFundFlowExpert(router=router)
    contribs = await collect_contributions(
        ticker=ticker, ts=ts, cal_table=cal_table,
        fundamental_expert=fundamental,
        time_expert=time_expert,
        fund_flow_expert=fund_flow,
    )
    return predict(
        ticker=ticker, horizon=horizon, contributions=contribs,
        cal_table=cal_table, issued_at=ts,
    )


def cmd_calibrate(args: argparse.Namespace) -> int:
    table = (
        synthetic_calibration_for_mock() if args.mock else load_calibration()
    )
    out_path = args.out or table.snapshot_path
    written = table.write_parquet(out_path)
    print("=== GLOSTAT Calibrate ===")
    print(f"  source       : {'synthetic_mock' if args.mock else 'cache/'}")
    print(f"  theses found : {len(table.entries)}")
    print(f"  output       : {written}")
    print()
    print(f"{'thesis':<22} {'AUC':>6} {'Sharpe':>8} {'n':>6} {'Brier':>7} active")
    print(f"{'-' * 22} {'-' * 6} {'-' * 8} {'-' * 6} {'-' * 7} {'-' * 6}")
    for cal in sorted(table.entries.values(), key=lambda c: c.name):
        from glostat.predictor.calibration import is_active  # noqa: PLC0415
        active = "YES" if is_active(cal) else "no"
        print(
            f"{cal.name:<22} {cal.auc:>6.3f} {cal.sharpe:>+8.3f} "
            f"{cal.n_samples:>6} {cal.brier_score:>7.4f} {active}"
        )
    return 0


def _load_fixture(ticker: str) -> dict[str, Any]:
    path = _FIXTURES_DIR / f"{ticker.lower()}_mock.json"
    if not path.exists():
        raise FileNotFoundError(
            f"no mock fixture for {ticker} at {path}"
        )
    return json.loads(path.read_text("utf-8"))


__all__ = [
    "add_calibrate_subparser",
    "add_predict_subparser",
    "cmd_calibrate",
    "cmd_predict",
]
