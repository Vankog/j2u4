"""Tests for the retry helper in clients.py.

Cover the contract: retry only on transient (5xx + connection/timeout),
return immediately on 4xx, raise ApiError when every attempt fails at
the network level.
"""

from unittest.mock import patch, MagicMock

import pytest
import requests

from j2u4.clients import ApiError, _get_with_retry, _RETRY_ATTEMPTS


def _response(status_code: int) -> MagicMock:
    r = MagicMock(spec=requests.Response)
    r.status_code = status_code
    r.ok = 200 <= status_code < 300
    r.reason = "Mocked"
    return r


def test_retries_on_502_then_succeeds():
    """Two 502s then a 200 → caller gets the 200, no exception."""
    responses = [_response(502), _response(502), _response(200)]
    with patch("j2u4.clients.requests.get", side_effect=responses) as mock_get:
        r = _get_with_retry("https://example/foo", "Tempo", _sleep=lambda s: None)
    assert r.status_code == 200
    assert mock_get.call_count == 3


def test_no_retry_on_401():
    """4xx is a real error — return immediately, do not retry."""
    with patch(
        "j2u4.clients.requests.get", return_value=_response(401)
    ) as mock_get:
        r = _get_with_retry("https://example/foo", "Tempo", _sleep=lambda s: None)
    assert r.status_code == 401
    assert mock_get.call_count == 1


def test_no_retry_on_404():
    """get_worklog relies on 404=None semantics — must not retry."""
    with patch(
        "j2u4.clients.requests.get", return_value=_response(404)
    ) as mock_get:
        r = _get_with_retry("https://example/foo", "Tempo", _sleep=lambda s: None)
    assert r.status_code == 404
    assert mock_get.call_count == 1


def test_returns_last_5xx_when_all_attempts_exhausted():
    """If every retry comes back 5xx, hand the last response to the caller
    so they can build a status-specific ApiError via _handle_api_error."""
    with patch(
        "j2u4.clients.requests.get", return_value=_response(503)
    ) as mock_get:
        r = _get_with_retry("https://example/foo", "Tempo", _sleep=lambda s: None)
    assert r.status_code == 503
    assert mock_get.call_count == _RETRY_ATTEMPTS


def test_raises_apierror_on_persistent_connection_failure():
    """All attempts fail at the network level → ApiError mentioning the service."""
    with patch(
        "j2u4.clients.requests.get",
        side_effect=requests.exceptions.ConnectionError("nope"),
    ) as mock_get:
        with pytest.raises(ApiError) as exc:
            _get_with_retry("https://example/foo", "Tempo", _sleep=lambda s: None)
    assert "Tempo" in str(exc.value)
    assert mock_get.call_count == _RETRY_ATTEMPTS


def test_raises_apierror_on_persistent_timeout():
    """Same for Timeout — message should mention timed out."""
    with patch(
        "j2u4.clients.requests.get",
        side_effect=requests.exceptions.Timeout("slow"),
    ) as mock_get:
        with pytest.raises(ApiError) as exc:
            _get_with_retry("https://example/foo", "Jira", _sleep=lambda s: None)
    assert "Jira" in str(exc.value)
    assert "timed out" in str(exc.value).lower()
    assert mock_get.call_count == _RETRY_ATTEMPTS


def test_connection_error_recovers_on_third_attempt():
    """Mix of network failure and final success — caller sees the 200."""
    responses_or_exc = [
        requests.exceptions.ConnectionError("first"),
        requests.exceptions.Timeout("second"),
        _response(200),
    ]
    with patch(
        "j2u4.clients.requests.get", side_effect=responses_or_exc
    ) as mock_get:
        r = _get_with_retry("https://example/foo", "Tempo", _sleep=lambda s: None)
    assert r.status_code == 200
    assert mock_get.call_count == 3
