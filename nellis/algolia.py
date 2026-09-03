"""Talk to Algolia and turn its JSON hits into our Listing objects.

Two jobs:
  1. POST a query (built by query_mapping) to the right Algolia index.
  2. Map each raw hit dict -> a clean Listing.

Everything goes through http.request() (throttle, timeout, error translation).
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from . import http
from .config import ALGOLIA_API_KEY, ALGOLIA_APP_ID
from .errors import NellisParseError
from .models import Listing, SearchResult



# Algolia host for queries. The index name goes in the URL path.
def _query_url(index: str) -> str:
    return f"https://{ALGOLIA_APP_ID}-dsn.algolia.net/1/indexes/{index}/query"


def _headers() -> dict[str, str]:
    return {
        "X-Algolia-Application-Id": ALGOLIA_APP_ID,
        "X-Algolia-API-Key": ALGOLIA_API_KEY,
        "Content-Type": "application/json",
    }


def _hit_to_listing(hit: dict) -> Listing:
    """Map one Algolia hit dict -> Listing.

    Known hit fields (from the probe):
      objectID, "Lead Description" (title), "Photo", "Location Name",
      "Time Remaining" (unix ts, float), "Item Condition", "Brand",
      "Taxonomy Level 1", "Auction Event Type", "Auction Event Name",
      "Star Rating", and condition flags "Is Damaged" / "Is Functional" /
      "Missing Parts" / "In Package" / "Assembly Required" ("Yes"/"No" strings).
    """
    pid = str(hit.get("objectID"))

    # build the product URL. Format: https://www.nellisauction.com/p/<slug>/<id>
    #   A slug isn't in the hit; a simple option is f".../p/x/{pid}" (Nellis routes by id).
    product_URL = f"https://www.nellisauction.com/p/x/{pid}"

    # convert "Time Remaining" (a unix timestamp float, or None) -> a datetime.
    #   NOTE: despite the name, this value is the absolute close time, not a duration.
    ts = hit.get("Time Remaining")
    closes_at = datetime.fromtimestamp(ts, tz=timezone.utc) if ts else None

    # the condition flags come as "Yes"/"No" strings -> convert to bool/None via the helper.
    is_damaged = _yes_no_converter(hit.get("Is Damaged"))
    is_functional = _yes_no_converter(hit.get("Is Functional"))
    missing_parts = _yes_no_converter(hit.get("Missing Parts"))
    in_package = _yes_no_converter(hit.get("In Package"))
    assembly_required = _yes_no_converter(hit.get("Assembly Required"))

    est_retail = hit.get("Suggested Retail")

    # current_bid / bid_count stay None here — Algolia has no live bid data at
    #   all (that comes from detail_source.fetch_pricing later). est_retail is a
    #   static per-item value Algolia does have, so we map it directly below.
    return Listing(
        id=pid,
        title=hit.get("Lead Description"),
        url=product_URL,
        closes_at=closes_at,
        est_retail=Decimal(str(est_retail)) if est_retail is not None else None,
        is_damaged=is_damaged,
        is_functional=is_functional,
        missing_parts=missing_parts,
        in_package=in_package,
        assembly_required=assembly_required,
        location=hit.get("Location Name"),
        brand=hit.get("Brand"),
        category=hit.get("Taxonomy Level 1"),
        star_rating=hit.get("Star Rating"),
        item_condition=hit.get("Item Condition"),
        fetched_at=datetime.now(timezone.utc),
    )

def _yes_no_converter(value: str | None) -> bool | None:
    """Map Algolia's "Yes"/"No" strings to a bool, or None if absent. Helper function for _hit_to_listing"""
    if value is None:
        return None
    return value == "Yes"

def search(request: dict) -> SearchResult:
    """POST a query (from query_mapping.to_algolia_request) and return a SearchResult."""
    index = request["index"]
    params = request["params"]

    response = http.request("POST", _query_url(index), headers=_headers(), json=params)

    # parse the JSON into a dict; raise if the expected "hits" key is missing.
    data = response.json()
    if "hits" not in data:
        raise NellisParseError("Algolia response missing 'hits'")

    hits = data["hits"]

    # convert each raw hit dict into a clean Listing.
    listings = []
    for hit in hits:
        listings.append(_hit_to_listing(hit))

    algolia_page = data.get("page", 0)
    return SearchResult(
        listings=listings,
        total_count=data.get("nbHits"),
        page=algolia_page + 1,
        has_more=algolia_page + 1 < data.get("nbPages", 0),
        source="algolia",
    )


