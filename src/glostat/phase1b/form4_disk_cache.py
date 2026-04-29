from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Final

import structlog

from glostat.data.sec_edgar_form4 import Form4Transaction

log: Final = structlog.get_logger(__name__)

_DEFAULT_DIR: Final[Path] = Path("cache") / "phase1b" / "form4"


def cache_path(ticker: str, days_back: int, base: Path = _DEFAULT_DIR) -> Path:
    return base / f"{ticker.upper()}_d{days_back}.json"


def load(ticker: str, days_back: int, base: Path = _DEFAULT_DIR) -> list[Form4Transaction] | None:
    path = cache_path(ticker, days_back, base)
    if not path.exists():
        return None
    try:
        raw = json.loads(path.read_text())
        return [
            Form4Transaction(
                issuer_cik=str(r["issuer_cik"]),
                accession=str(r["accession"]),
                filed_at=date.fromisoformat(r["filed_at"]),
                transaction_date=date.fromisoformat(r["transaction_date"]),
                reporter_name=str(r["reporter_name"]),
                reporter_cik=str(r["reporter_cik"]),
                reporter_role=str(r["reporter_role"]),
                code=str(r["code"]),
                shares=float(r["shares"]),
                price=float(r["price"]),
                value_usd=float(r["value_usd"]),
            )
            for r in raw["transactions"]
        ]
    except Exception as exc:
        log.warning("form4_cache.load_failed", path=str(path), err=str(exc))
        return None


def save(
    ticker: str,
    days_back: int,
    transactions: list[Form4Transaction],
    base: Path = _DEFAULT_DIR,
) -> Path:
    path = cache_path(ticker, days_back, base)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "ticker": ticker.upper(),
        "days_back": days_back,
        "n": len(transactions),
        "transactions": [
            {
                "issuer_cik": t.issuer_cik,
                "accession": t.accession,
                "filed_at": t.filed_at.isoformat(),
                "transaction_date": t.transaction_date.isoformat(),
                "reporter_name": t.reporter_name,
                "reporter_cik": t.reporter_cik,
                "reporter_role": t.reporter_role,
                "code": t.code,
                "shares": t.shares,
                "price": t.price,
                "value_usd": t.value_usd,
            }
            for t in transactions
        ],
    }
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, separators=(",", ":")))
    tmp.replace(path)
    return path


__all__ = ["cache_path", "load", "save"]
