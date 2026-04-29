from __future__ import annotations

import argparse
import asyncio
import os
from datetime import date
from pathlib import Path
from typing import Final

import structlog

from glostat.data.sec_edgar_client import SecEdgarClient
from glostat.data.yfinance_client import YFinanceClient
from glostat.phase1b.orchestrator import run_all_theses, write_comparison_md

log: Final = structlog.get_logger(__name__)

_SP500_PATH: Final[Path] = (
    Path(__file__).resolve().parents[3] / "configs" / "universes" / "sp500_top50.txt"
)
_RUSSELL_PATH: Final[Path] = (
    Path(__file__).resolve().parents[3]
    / "configs" / "universes" / "russell2k_top200_proxy.txt"
)


def load_tickers(path: Path) -> list[str]:
    out: list[str] = []
    for line in path.read_text().splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        out.append(s.upper())
    return out


async def resolve_ciks(
    tickers: list[str], sec_client: SecEdgarClient
) -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []
    for t in tickers:
        try:
            cik = await sec_client.ticker_to_cik(t)
            pairs.append((t, cik))
        except KeyError:
            log.warning("phase1b.cik_not_found", ticker=t)
    return pairs


async def run(args: argparse.Namespace) -> int:
    start = date.fromisoformat(args.start)
    end = date.fromisoformat(args.end)
    yf_client = YFinanceClient()
    sec_user_agent = os.environ.get("GLOSTAT_SEC_USER_AGENT")
    sec_client = SecEdgarClient(user_agent=sec_user_agent)

    sp500 = load_tickers(_SP500_PATH)
    russell_tickers = load_tickers(_RUSSELL_PATH)
    if args.russell_limit and args.russell_limit < len(russell_tickers):
        russell_tickers = russell_tickers[: args.russell_limit]
    russell_with_cik = await resolve_ciks(russell_tickers, sec_client)
    log.info(
        "phase1b.universes",
        sp500=len(sp500),
        russell_total=len(russell_tickers),
        russell_resolved=len(russell_with_cik),
    )

    results = await run_all_theses(
        start=start,
        end=end,
        sp500_universe=sp500,
        russell_universe=russell_with_cik,
        yf_client=yf_client,
        sec_client=sec_client,
    )
    out_path = write_comparison_md(results, start=start, end=end)
    log.info("phase1b.report_written", path=str(out_path))

    print("=" * 80)
    print(f"Phase 1B comparison report → {out_path}")
    print("=" * 80)
    for expert, (rep, gate) in results.items():
        print(
            f"  {expert:<20} sharpe={rep.overall_sharpe:+.3f}  "
            f"auc={rep.overall_auc:.3f}  cost={rep.cost_passed_pct * 100:5.1f}%  "
            f"n={rep.n_signals:>4}  gate={gate.pass_status}"
        )
    print("=" * 80)

    await sec_client.aclose()
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Phase 1B — empirical hindcast of 4 free-stack alpha theses."
    )
    p.add_argument("--start", default="2024-01-01", help="ISO start date")
    p.add_argument("--end", default="2026-03-29", help="ISO end date")
    p.add_argument(
        "--russell-limit",
        type=int,
        default=0,
        help="Cap russell-2k proxy universe (0 = no cap; useful to keep SEC fetch budget bounded)",
    )
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return asyncio.run(run(args))


if __name__ == "__main__":
    raise SystemExit(main())
