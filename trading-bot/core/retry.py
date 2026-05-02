"""Retry helper for transient network/broker errors.

Two scan failures in the week of 2026-04-29 to 2026-05-01 came from short-lived
network blips — one SSL handshake reset, one TCP "Connection reset by peer" —
against Alpaca/yfinance. Both surfaced as `requests.exceptions.ConnectionError`
or `urllib3.exceptions.ProtocolError` and aborted the entire scan job, costing
us a full day's A/B candidate slate.

This module wraps a callable in a small retry loop that targets that exact
class of error (network/transport hiccup), with linear backoff. Anything that
isn't a recognized transient error propagates immediately so we still fail
loudly on real bugs (auth issues, validation errors, bot bugs, etc).

Usage:
    from core.retry import with_network_retries
    snapshots = with_network_retries(
        lambda: client.get_snapshots(symbols),
        label="get_snapshots",
    )
"""
from __future__ import annotations

import logging
import time
from typing import Any, Callable, TypeVar

logger = logging.getLogger(__name__)

# Lazy-imported error classes — these are the transient transport-layer errors
# we treat as worth retrying. Anything else (auth, validation, bot bugs)
# propagates so _track_job fires JOB FAILED. Importing here once avoids a
# per-call import overhead inside the hot loop.
try:
    from requests.exceptions import (
        ConnectionError as RequestsConnectionError,
        Timeout as RequestsTimeout,
        ChunkedEncodingError as RequestsChunkedError,
    )
    from urllib3.exceptions import (
        ProtocolError as Urllib3ProtocolError,
        TimeoutError as Urllib3TimeoutError,
    )
    _RETRY_EXC: tuple[type[BaseException], ...] = (
        RequestsConnectionError,
        RequestsTimeout,
        RequestsChunkedError,
        Urllib3ProtocolError,
        Urllib3TimeoutError,
        ConnectionResetError,
        ConnectionAbortedError,
        TimeoutError,
    )
except ImportError:
    # Fall back to stdlib socket errors if requests/urllib3 unavailable
    # (very unlikely in production but keeps tests importable).
    _RETRY_EXC = (
        ConnectionResetError,
        ConnectionAbortedError,
        TimeoutError,
    )


T = TypeVar("T")


def with_network_retries(
    fn: Callable[[], T],
    *,
    label: str = "network call",
    attempts: int = 3,
    base_sleep_secs: float = 2.0,
) -> T:
    """Run ``fn`` with up to ``attempts`` tries on transient network errors.

    Args:
        fn: zero-arg callable to execute. Wrap any callsite in ``lambda:``.
        label: human-friendly identifier for log messages
            (e.g. "get_snapshots", "get_tradable_universe").
        attempts: total tries including the first call. Default 3.
        base_sleep_secs: linear backoff base — sleep N seconds between
            attempt N and attempt N+1. Default 2s → sleeps 2s, 4s.

    Returns:
        Whatever ``fn`` returns on success.

    Raises:
        The original exception once attempts are exhausted, OR any
        non-retryable exception immediately (auth, validation, etc).
    """
    last_err: BaseException | None = None
    for attempt in range(1, attempts + 1):
        try:
            return fn()
        except _RETRY_EXC as e:
            last_err = e
            if attempt < attempts:
                sleep_for = base_sleep_secs * attempt
                logger.warning(
                    "%s: attempt %d/%d failed with %s: %s — retrying in %.1fs",
                    label, attempt, attempts, type(e).__name__, e, sleep_for,
                )
                time.sleep(sleep_for)
            else:
                logger.error(
                    "%s: attempt %d/%d failed with %s: %s — no retries left",
                    label, attempt, attempts, type(e).__name__, e,
                )
    assert last_err is not None  # only reached after a retryable exception
    raise last_err
