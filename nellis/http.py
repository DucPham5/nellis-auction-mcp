"""Shared HTTP plumbing for every data source.

Centralises what all of Algolia / cargo / account calls share: consistent
timeouts and User-Agent, light client-side throttling, optional session-cookie
injection for authenticated endpoints, and translation of raw ``httpx`` failures
into our ``NellisError`` hierarchy.

Algolia and cargo live on different hosts, so we deliberately do NOT pin a
``base_url`` — callers pass full URLs.

This is a SKELETON — fill in the TODO bodies.
"""

from __future__ import annotations

import threading
import time

import httpx

from .config import session_cookie  # re-exported for callers that need it
from .errors import NellisBlockedError, NellisNetworkError

USER_AGENT = "nellis-auction-mcp/0.1 (personal MCP server)"
DEFAULT_TIMEOUT = httpx.Timeout(connect=5.0, read=15.0, write=15.0, pool=5.0)
MIN_REQUEST_INTERVAL = 1.0  # min seconds between outbound requests (be polite)

__all__ = ["USER_AGENT", "get_client", "session_cookie", "request"]

_throttle_lock = threading.Lock()
_last_request_at = 0.0
_shared_client: httpx.Client | None = None


def _throttle() -> None:
    """Block until MIN_REQUEST_INTERVAL has passed since the last request."""
    global _last_request_at
    with _throttle_lock:
        wait = MIN_REQUEST_INTERVAL - (time.monotonic() - _last_request_at)
        if wait > 0:
            time.sleep(wait)

        _last_request_at = time.monotonic()


def get_client() -> httpx.Client:
    """Return a lazily-created shared client for anonymous (public) calls."""
    global _shared_client
    if _shared_client is None:
        _shared_client = httpx.Client(timeout=DEFAULT_TIMEOUT, headers={"User-Agent": USER_AGENT}, follow_redirects=True)
    return _shared_client


def request(
    method: str,
    url: str,
    *,
    client: httpx.Client | None = None,
    **kwargs,
) -> httpx.Response:
    """Send one request through the throttle and normalise errors.

    Raises:
        NellisNetworkError: connection / timeout problems.
        NellisBlockedError: HTTP 403 or 429 (do not auto-retry).

    Other non-2xx statuses are returned as-is (e.g. a JSON 404 body that still
    carries a useful message).
    """
    client = client or get_client()
    _throttle()
    try:
        response = client.request(method, url, **kwargs)
    except httpx.TimeoutException as exc:
        raise NellisNetworkError(f"Timed out reaching {url}") from exc
    except httpx.TransportError as exc:
        raise NellisNetworkError(f"Could not reach {url}: {exc}") from exc

    if response.status_code in (403, 429):
        raise NellisBlockedError(
            f"Nellis pushed back (HTTP {response.status_code}) on {url} — "
            "likely rate-limited or blocked. Wait a bit and try again."
        )

    return response
