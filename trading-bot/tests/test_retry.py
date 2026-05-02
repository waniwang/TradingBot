"""Tests for core.retry.with_network_retries.

Locks in the retry contract added 2026-05-01 after two scan failures from
transient network errors (SSL handshake reset on 2026-04-29, connection
reset by peer on 2026-05-01) wiped out the day's A/B candidate slate.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from requests.exceptions import ConnectionError as RequestsConnectionError

from core.retry import with_network_retries


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    """Skip the backoff sleeps so retry tests run instantly."""
    import core.retry as r
    monkeypatch.setattr(r.time, "sleep", lambda *_: None)


class TestWithNetworkRetries:
    def test_returns_value_on_first_success(self):
        fn = MagicMock(return_value=42)
        assert with_network_retries(fn, label="test") == 42
        assert fn.call_count == 1

    def test_retries_then_succeeds(self):
        """Transient ConnectionError on first attempt, success on second."""
        fn = MagicMock(side_effect=[
            RequestsConnectionError("Connection aborted"),
            "success",
        ])
        assert with_network_retries(fn, label="test", attempts=3) == "success"
        assert fn.call_count == 2

    def test_raises_after_attempts_exhausted(self):
        fn = MagicMock(side_effect=ConnectionResetError(104, "reset by peer"))
        with pytest.raises(ConnectionResetError, match="reset by peer"):
            with_network_retries(fn, label="test", attempts=3)
        assert fn.call_count == 3

    def test_non_retryable_propagates_immediately(self):
        """A real bug (e.g. ValueError) must not be swallowed or retried."""
        fn = MagicMock(side_effect=ValueError("not a network problem"))
        with pytest.raises(ValueError, match="not a network problem"):
            with_network_retries(fn, label="test", attempts=3)
        assert fn.call_count == 1

    def test_403_forbidden_propagates_immediately(self):
        """An auth/permission failure (HTTP 403, today's wash-trade error)
        is NOT a transient network problem — must NOT be retried."""
        from requests.exceptions import HTTPError
        fn = MagicMock(side_effect=HTTPError("403 Client Error: Forbidden"))
        with pytest.raises(HTTPError):
            with_network_retries(fn, label="test", attempts=3)
        assert fn.call_count == 1

    def test_timeout_is_retryable(self):
        from requests.exceptions import Timeout
        fn = MagicMock(side_effect=[Timeout("read timed out"), "ok"])
        assert with_network_retries(fn, label="test") == "ok"
        assert fn.call_count == 2

    def test_urllib3_protocol_error_is_retryable(self):
        from urllib3.exceptions import ProtocolError
        fn = MagicMock(side_effect=[ProtocolError("connection broken"), "ok"])
        assert with_network_retries(fn, label="test") == "ok"
        assert fn.call_count == 2
