"""Authenticated account data — active bids and watchlist.

Both come from ONE Nellis endpoint: the Remix loader behind the "My Auctions"
dashboard page (found via a logged-in DevTools capture — same Remix loader
pattern already used for product-page pricing in detail_source.py, just called
directly instead of scraped out of HTML). It returns every lot the user is
currently watching and/or has bid on in one list; get_active_bids() and
get_watchlist() split it by filtering the per-lot `userState` flags, since
Nellis's own UI does the same rather than exposing two separate endpoints.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from . import http
from .config import session_cookie
from .errors import NellisAuthError, NellisParseError
from .models import AccountListing

_ACTIVE_AUCTIONS_URL = (
    "https://www.nellisauction.com/dashboard/auctions/active"
    "?_data=routes%2Fdashboard.auctions.active"
)


def _fetch_records() -> list[dict]:
    """Call the dashboard loader and return its raw `myAuctions.records` list."""
    cookie = session_cookie()
    if cookie is None:
        raise NellisAuthError(
            "NELLIS_SESSION_COOKIE is not set. Log into nellisauction.com, copy "
            "the __session cookie's value from DevTools -> Application -> "
            "Cookies, and set it in .env."
        )

    response = http.request(
        "GET",
        _ACTIVE_AUCTIONS_URL,
        headers={"Cookie": f"__session={cookie}"},
    )

    if response.status_code in (401, 403):
        raise NellisAuthError(
            f"Nellis rejected the session cookie (HTTP {response.status_code}) — "
            "it may have expired. Log in again and refresh NELLIS_SESSION_COOKIE."
        )

    try:
        data = response.json()
        return data["myAuctions"]["records"]
    except (ValueError, KeyError, TypeError) as exc:
        raise NellisParseError(
            "Unexpected response from the active-auctions endpoint — Nellis may "
            "have changed the dashboard page, or the session cookie is invalid."
        ) from exc


def _record_to_listing(record: dict) -> AccountListing:
    pid = str(record["id"])
    product_url = f"https://www.nellisauction.com/p/x/{pid}"

    photos = record.get("photos") or []
    image_url = photos[0]["url"] if photos else None

    grade = record.get("grade") or {}
    condition_type = (grade.get("conditionType") or {}).get("description")
    damage = (grade.get("damageType") or {}).get("description")
    functional = (grade.get("functionalType") or {}).get("description")
    missing_parts = (grade.get("missingPartsType") or {}).get("description")
    package = (grade.get("packageType") or {}).get("description")
    assembly = (grade.get("assemblyType") or {}).get("description")

    location = record.get("location") or {}
    user_state = record.get("userState") or {}

    close_time = (record.get("closeTime") or {}).get("value")
    closes_at = datetime.fromisoformat(close_time.replace("Z", "+00:00")) if close_time else None

    retail = record.get("retailPrice")
    current = record.get("currentPrice")
    next_bid = user_state.get("nextBid")

    return AccountListing(
        id=pid,
        title=record.get("title"),
        url=product_url,
        image_url=image_url,
        current_bid=Decimal(str(current)) if current is not None else None,
        next_bid=Decimal(str(next_bid)) if next_bid is not None else None,
        est_retail=Decimal(str(retail)) if retail is not None else None,
        bid_count=record.get("bidCount"),
        bidder_count=record.get("bidderCount"),
        watchlist_count=record.get("watchlistCount"),
        location=location.get("name"),
        closes_at=closes_at,
        market_status=record.get("marketStatus"),
        item_condition=condition_type,
        star_rating=grade.get("rating"),
        is_damaged=None if damage is None else damage != "None",
        is_functional=None if functional is None else functional == "Yes",
        missing_parts=None if missing_parts is None else missing_parts != "No",
        in_package=None if package is None else package == "Yes",
        assembly_required=None if assembly is None else assembly == "Yes",
        is_watching=bool(user_state.get("isWatching")),
        has_participated=bool(user_state.get("hasParticipated")),
        is_winning=bool(user_state.get("isWinning")),
        is_allowed_to_bid=bool(user_state.get("isAllowedToBid")),
    )


def get_active_bids() -> list[AccountListing]:
    """Lots you've placed at least one bid on."""
    records = _fetch_records()
    return [_record_to_listing(r) for r in records if (r.get("userState") or {}).get("hasParticipated")]


def get_watchlist() -> list[AccountListing]:
    """Lots you're watching (independent of whether you've bid)."""
    records = _fetch_records()
    return [_record_to_listing(r) for r in records if (r.get("userState") or {}).get("isWatching")]
