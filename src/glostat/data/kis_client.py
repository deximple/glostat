from __future__ import annotations

import asyncio
import json
import os
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any, Final

import httpx
import structlog

from glostat.core.errors import GlostatError
from glostat.data.snapshot_broker import SnapshotBroker, SnapshotKey

# v1.4 N1 — KIS Open API REST client (read-only paths only).
# Source: https://apiportal.koreainvestment.com/
#
# WHY: KIS provides real-time intraday investor flows (외인/기관/개인 net buy)
# that yfinance + DART do not expose. Free-tier (개인 모의투자 + 실전) supports
# 20 req/sec, enough for the predict path. Order-execution endpoints (TTTC0802U,
# VTTC0802U …) are intentionally NOT wrapped here — GLOSTAT is a prediction tool
# (INV-GS-101) and must never place orders.
#
# Graceful skip: when GLOSTAT_KIS_APP_KEY / GLOSTAT_KIS_APP_SECRET are unset
# every method raises KisCredentialsMissingError so callers can fall through
# to Naver / Toss / DART without faking data.

log: Final = structlog.get_logger(__name__)

_BASE_LIVE: Final = "https://openapi.koreainvestment.com:9443"
_BASE_PAPER: Final = "https://openapivts.koreainvestment.com:29443"
_RATE_LIMIT_PER_SEC: Final[int] = 20
_MIN_INTERVAL_S: Final[float] = 1.0 / _RATE_LIMIT_PER_SEC
_DEFAULT_TIMEOUT: Final[httpx.Timeout] = httpx.Timeout(20.0, connect=5.0)
_TOKEN_REFRESH_MARGIN_S: Final[int] = 600  # refresh 10 minutes before expiry
_OAUTH_PATH: Final = "/oauth2/tokenP"
_INVESTOR_PATH: Final = "/uapi/domestic-stock/v1/quotations/inquire-investor"
_DAILY_FLOW_PATH: Final = "/uapi/domestic-stock/v1/quotations/inquire-daily-trade"
_TR_INVESTOR: Final = "FHKST01010900"  # 종목별 투자자별 매매동향 (real-time intraday)
_TR_DAILY_FLOW: Final = "FHKST01010800"  # 종목별 일별 매매동향 summary


class KisCredentialsMissingError(NotImplementedError):
    """Raised when GLOSTAT_KIS_APP_KEY / SECRET are not configured."""

    @classmethod
    def make(cls) -> KisCredentialsMissingError:
        return cls(
            "KIS credentials missing. Register at "
            "https://apiportal.koreainvestment.com/ (free) and export "
            "GLOSTAT_KIS_APP_KEY=<key> + GLOSTAT_KIS_APP_SECRET=<secret>. "
            "Without these, KR intraday investor flows fall back to Naver. "
            "See docs/KIS_API_SETUP.md for the step-by-step setup."
        )


class KisApiError(GlostatError):
    """Raised when KIS returns a non-recoverable HTTP / business error."""


def _resolve_credentials(
    *, app_key: str | None = None, app_secret: str | None = None,
) -> tuple[str, str]:
    key = app_key or os.environ.get("GLOSTAT_KIS_APP_KEY")
    secret = app_secret or os.environ.get("GLOSTAT_KIS_APP_SECRET")
    if not key or not key.strip() or not secret or not secret.strip():
        raise KisCredentialsMissingError.make()
    return key.strip(), secret.strip()


def is_kis_configured() -> bool:
    """Cheap check used by experts to decide whether to attempt a KIS call."""
    key = os.environ.get("GLOSTAT_KIS_APP_KEY")
    secret = os.environ.get("GLOSTAT_KIS_APP_SECRET")
    return bool(key and key.strip() and secret and secret.strip())


@dataclass(frozen=True, slots=True)
class KisIntradayFlow:
    code: str
    snapped_at: datetime
    foreign_net: float           # 외국인 순매수 (shares; negative = net sell)
    institutional_net: float     # 기관 순매수
    individual_net: float        # 개인 순매수
    pgm_net: float = 0.0         # 프로그램 순매수 (when available)
    source: str = "kis"


@dataclass(frozen=True, slots=True)
class KisDailySummary:
    code: str
    bar_date: date
    foreign_net_won: float       # 외국인 순매수 (KRW)
    institutional_net_won: float
    individual_net_won: float
    source: str = "kis"


@dataclass(slots=True)
class _Throttle:
    rate_per_sec: int = _RATE_LIMIT_PER_SEC
    _next_slot: float = field(default=0.0, init=False)
    _lock: asyncio.Lock = field(init=False)
    acquire_count: int = field(default=0, init=False)
    throttled_count: int = field(default=0, init=False)

    def __post_init__(self) -> None:
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        async with self._lock:
            now = time.monotonic()
            wait = max(0.0, self._next_slot - now)
            if wait > 0:
                self.throttled_count += 1
            self._next_slot = max(now, self._next_slot) + (1.0 / self.rate_per_sec)
            self.acquire_count += 1
        if wait > 0:
            await asyncio.sleep(wait)


class KisClient:
    """KIS OpenAPI REST client (read-only domestic equities paths).

    Order-execution endpoints intentionally NOT wrapped — INV-GS-101 forbids
    BUY/SELL output, INV-GS-024 forbids broadcast; placing orders would breach
    the prediction-tool framing.
    """

    def __init__(
        self,
        *,
        app_key: str | None = None,
        app_secret: str | None = None,
        client: httpx.AsyncClient | None = None,
        snapshot_broker: SnapshotBroker | None = None,
        paper: bool = False,
    ) -> None:
        self._app_key, self._app_secret = _resolve_credentials(
            app_key=app_key, app_secret=app_secret,
        )
        self._client = client or httpx.AsyncClient(timeout=_DEFAULT_TIMEOUT)
        self._broker = snapshot_broker
        self._base_url = _BASE_PAPER if paper else _BASE_LIVE
        self._throttle = _Throttle()
        self._access_token: str | None = None
        self._token_expires_at: float = 0.0
        self._token_lock = asyncio.Lock()
        self._last_snapshot_id: str | None = None

    @property
    def last_snapshot_id(self) -> str | None:
        return self._last_snapshot_id

    @property
    def throttle(self) -> _Throttle:
        return self._throttle

    async def aclose(self) -> None:
        await self._client.aclose()

    # ── auth ─────────────────────────────────────────────────────────────

    async def _ensure_token(self) -> str:
        async with self._token_lock:
            now = time.monotonic()
            if self._access_token is not None and now < self._token_expires_at:
                return self._access_token
            await self._throttle.acquire()
            payload = {
                "grant_type": "client_credentials",
                "appkey": self._app_key,
                "appsecret": self._app_secret,
            }
            try:
                resp = await self._client.post(
                    self._base_url + _OAUTH_PATH, json=payload,
                )
                resp.raise_for_status()
            except httpx.HTTPError as exc:
                raise KisApiError(f"KIS token request failed: {exc}") from exc
            try:
                data = resp.json()
            except ValueError as exc:
                raise KisApiError(f"KIS token non-JSON response: {exc}") from exc
            token = data.get("access_token")
            ttl = int(data.get("expires_in", 0) or 0)
            if not token or ttl <= 0:
                raise KisApiError(
                    f"KIS token response missing fields: keys={list(data)}"
                )
            self._access_token = str(token)
            self._token_expires_at = now + max(60, ttl - _TOKEN_REFRESH_MARGIN_S)
            return self._access_token

    # ── public read-only methods ─────────────────────────────────────────

    async def get_intraday_flows(self, ticker: str) -> KisIntradayFlow:
        code = _normalize_code(ticker)
        token = await self._ensure_token()
        await self._throttle.acquire()
        url = self._base_url + _INVESTOR_PATH
        params = {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": code}
        data = await self._get_json(url, token, _TR_INVESTOR, params)
        rows = data.get("output", []) or []
        if not rows:
            raise KisApiError(f"KIS intraday: empty payload for {code}")
        latest = rows[0] if isinstance(rows, list) else rows
        flow = KisIntradayFlow(
            code=code,
            snapped_at=datetime.now(tz=UTC),
            foreign_net=_parse_signed(latest.get("frgn_ntby_qty", "0")),
            institutional_net=_parse_signed(latest.get("orgn_ntby_qty", "0")),
            individual_net=_parse_signed(latest.get("prsn_ntby_qty", "0")),
            pgm_net=_parse_signed(latest.get("pgm_ntby_qty", "0")),
        )
        self._record_snapshot(
            tool="kis.inquire-investor",
            uaid=f"XKRX.{code}",
            edge_type="intraday_flow",
            ts=flow.snapped_at,
            params={"code": code, "tr_id": _TR_INVESTOR},
            payload={
                "code": code,
                "foreign_net": flow.foreign_net,
                "institutional_net": flow.institutional_net,
                "individual_net": flow.individual_net,
            },
        )
        return flow

    async def get_daily_summary(self, ticker: str) -> KisDailySummary:
        code = _normalize_code(ticker)
        token = await self._ensure_token()
        await self._throttle.acquire()
        url = self._base_url + _DAILY_FLOW_PATH
        params = {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": code}
        data = await self._get_json(url, token, _TR_DAILY_FLOW, params)
        rows = data.get("output", []) or []
        if not rows:
            raise KisApiError(f"KIS daily: empty payload for {code}")
        latest = rows[0] if isinstance(rows, list) else rows
        summary = KisDailySummary(
            code=code,
            bar_date=_parse_date(latest.get("stck_bsop_date", "")) or date.today(),
            foreign_net_won=_parse_signed(latest.get("frgn_ntby_tr_pbmn", "0")),
            institutional_net_won=_parse_signed(latest.get("orgn_ntby_tr_pbmn", "0")),
            individual_net_won=_parse_signed(latest.get("prsn_ntby_tr_pbmn", "0")),
        )
        self._record_snapshot(
            tool="kis.inquire-daily-trade",
            uaid=f"XKRX.{code}",
            edge_type="daily_summary",
            ts=datetime.now(tz=UTC),
            params={"code": code, "tr_id": _TR_DAILY_FLOW},
            payload={
                "code": code,
                "bar_date": summary.bar_date.isoformat(),
                "foreign_net_won": summary.foreign_net_won,
            },
        )
        return summary

    # ── http + snapshot helpers ──────────────────────────────────────────

    async def _get_json(
        self, url: str, token: str, tr_id: str, params: Mapping[str, str],
    ) -> dict[str, Any]:
        headers = {
            "content-type": "application/json; charset=utf-8",
            "authorization": f"Bearer {token}",
            "appkey": self._app_key,
            "appsecret": self._app_secret,
            "tr_id": tr_id,
        }
        try:
            resp = await self._client.get(url, params=dict(params), headers=headers)
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise KisApiError(f"KIS GET failed url={url}: {exc}") from exc
        try:
            data = resp.json()
        except ValueError as exc:
            raise KisApiError(f"KIS non-JSON response from {url}: {exc}") from exc
        rt = str(data.get("rt_cd", "0"))
        # 0 = OK; KIS occasionally returns "1" with msg_cd describing rate limits.
        if rt != "0":
            msg = data.get("msg1") or data.get("msg_cd") or "unknown"
            raise KisApiError(f"KIS API error rt_cd={rt} msg={msg}")
        return data

    def _record_snapshot(
        self, *, tool: str, uaid: str, edge_type: str, ts: datetime,
        params: dict[str, Any], payload: dict[str, Any],
    ) -> None:
        if self._broker is None:
            return
        try:
            key = SnapshotKey(
                uaid=uaid, edge_type=edge_type, ts_utc=ts, tool=tool,
                params_canon=json.dumps(params, sort_keys=True, separators=(",", ":")),
            )
            record = self._broker.save_snapshot(key, payload)
            self._last_snapshot_id = record.leaf.leaf_hash
        except Exception as exc:
            log.warning("kis.snapshot_failed", uaid=uaid, err=str(exc))


def _normalize_code(ticker: str) -> str:
    t = (ticker or "").strip().upper()
    if t.endswith(".KS") or t.endswith(".KQ"):
        t = t[:-3]
    if not (len(t) == 6 and t.isdigit()):
        raise KisApiError(
            f"KIS expects 6-digit KRX code, got {ticker!r}"
        )
    return t


def _parse_signed(raw: Any) -> float:
    if raw is None:
        return 0.0
    s = str(raw).strip().replace(",", "").replace("+", "")
    if not s or s == "-":
        return 0.0
    try:
        return float(s)
    except ValueError:
        return 0.0


def _parse_date(s: str) -> date | None:
    s = (s or "").strip()
    if len(s) != 8 or not s.isdigit():
        return None
    try:
        return date(int(s[:4]), int(s[4:6]), int(s[6:8]))
    except ValueError:
        return None


__all__ = [
    "KisApiError",
    "KisClient",
    "KisCredentialsMissingError",
    "KisDailySummary",
    "KisIntradayFlow",
    "is_kis_configured",
]
