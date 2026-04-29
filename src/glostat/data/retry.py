from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Final

import httpx
import structlog

# Sprint 4 PR #3 — exponential backoff retry helpers shared by yfinance + sec_edgar.
# Goal: cut the 93% throttle ratio observed in PR #2 by retrying transient
# failures (429/503/504, timeouts, empty bodies) instead of silently dropping
# them. NOT a wholesale "retry on anything" — we whitelist transient causes so
# real 4xx errors continue to surface immediately.

log: Final = structlog.get_logger(__name__)

_DEFAULT_MAX_RETRIES: Final[int] = 3
_DEFAULT_BASE_DELAY_S: Final[float] = 1.0
_DEFAULT_BACKOFF_FACTOR: Final[float] = 2.0
_RETRYABLE_HTTP_STATUS: Final[frozenset[int]] = frozenset({429, 503, 504})


@dataclass(slots=True)
class RetryStats:
    """Per-client retry counter exposed for the network summary."""

    retry_count: int = 0
    retry_429_count: int = 0
    retry_5xx_count: int = 0
    retry_empty_count: int = 0
    retry_timeout_count: int = 0


class RetryError(RuntimeError):
    """Raised after all retry attempts have been exhausted."""


def _retry_after_seconds(exc: BaseException) -> float | None:
    if not isinstance(exc, httpx.HTTPStatusError):
        return None
    response = getattr(exc, "response", None)
    if response is None:
        return None
    raw = response.headers.get("Retry-After")
    if not raw:
        return None
    try:
        return max(0.0, float(raw))
    except (TypeError, ValueError):
        return None


def _is_retryable(exc: BaseException) -> tuple[bool, str]:
    # WHY: classify so the caller bumps the right counter and so we can refuse
    # to retry real client errors (404/403/etc.) — those signal bad inputs.
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code if exc.response is not None else 0
        if status == 429:
            return True, "429"
        if status in _RETRYABLE_HTTP_STATUS:
            return True, "5xx"
        return False, "http_other"
    if isinstance(exc, (httpx.TimeoutException, asyncio.TimeoutError)):
        return True, "timeout"
    if isinstance(exc, (httpx.ConnectError, httpx.ReadError, httpx.RemoteProtocolError)):
        return True, "timeout"
    return False, "non_retryable"


async def _default_sleep(delay: float) -> None:
    # WHY: indirection so tests can monkey-patch ``glostat.data.retry._default_sleep``
    # without also patching the throttle's `asyncio.sleep` calls in the broader codebase.
    await asyncio.sleep(delay)


async def with_retry[T](
    func: Callable[[], Awaitable[T]],
    *,
    stats: RetryStats,
    is_empty: Callable[[T], bool] | None = None,
    max_retries: int = _DEFAULT_MAX_RETRIES,
    base_delay_s: float = _DEFAULT_BASE_DELAY_S,
    backoff_factor: float = _DEFAULT_BACKOFF_FACTOR,
    operation: str = "fetch",
    sleeper: Callable[[float], Awaitable[None]] | None = None,
) -> T:
    """Run *func* with exponential backoff on transient failures.

    The total attempts are ``max_retries + 1`` (one initial + N retries).
    Sleeps follow ``base_delay_s * backoff_factor ** attempt`` with the standard
    Retry-After override on 429 responses.
    """

    # WHY: route through ``_default_sleep`` so test monkey-patches on this module
    # don't accidentally patch the throttle's own asyncio.sleep calls.
    sleep = sleeper or _default_sleep
    attempt = 0
    while True:
        try:
            result = await func()
        except Exception as exc:
            retryable, kind = _is_retryable(exc)
            if not retryable or attempt >= max_retries:
                raise
            delay = _retry_after_seconds(exc) or base_delay_s * (backoff_factor ** attempt)
            stats.retry_count += 1
            if kind == "429":
                stats.retry_429_count += 1
            elif kind == "5xx":
                stats.retry_5xx_count += 1
            elif kind == "timeout":
                stats.retry_timeout_count += 1
            log.info(
                "retry.transient",
                operation=operation, kind=kind, attempt=attempt + 1,
                delay_s=round(delay, 3), err=str(exc),
            )
            await sleep(delay)
            attempt += 1
            continue
        if is_empty is not None and is_empty(result):
            if attempt >= max_retries:
                return result
            delay = base_delay_s * (backoff_factor ** attempt)
            stats.retry_count += 1
            stats.retry_empty_count += 1
            log.info(
                "retry.empty",
                operation=operation, attempt=attempt + 1, delay_s=round(delay, 3),
            )
            await sleep(delay)
            attempt += 1
            continue
        return result


__all__ = [
    "RetryError",
    "RetryStats",
    "with_retry",
]
