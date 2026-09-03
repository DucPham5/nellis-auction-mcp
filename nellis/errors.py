"""Custom exception hierarchy for the Nellis MCP server.

The rest of the code raises one of these so the FastMCP tool layer can surface a
clear message instead of a raw traceback. Callers that don't care about the
specific failure catch the base ``NellisError``.

"""

from __future__ import annotations


class NellisError(Exception):
    """Base for every error this package raises."""


class NellisNetworkError(NellisError):
    """Error Occured: Either connection failed, timed our or DNS doesn't resolve"""


class NellisBlockedError(NellisError):
    """Server pushed back with HTTP 403 or 429 (rate limited). Do NOT auto-retry."""


class NellisParseError(NellisError):
    """A response arrived but its shape wasn't what we expected (upstream changed)."""


class NellisAuthError(NellisError):
    """Session cookie is missing or expired — needed by the account tools."""
