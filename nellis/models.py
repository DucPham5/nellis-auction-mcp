"""Pydantic data models shared across the Nellis MCP server.

FastMCP reads these annotations to auto-generate each tool's input/output JSON
schema, so getting the types right here is what makes the tools self-documenting
to the assistant. Use ``Decimal`` for money (never float) to avoid rounding.

This is a SKELETON — fill in the TODO gaps.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import Enum

from pydantic import BaseModel, Field


# --------------------------------------------------------------------------- #
# Enums — closed sets confirmed on the live site. Each value should be the exact
# string Algolia uses as the facet value, so it can be passed straight into a query.
# --------------------------------------------------------------------------- #
class EventType(str, Enum):
    RETAIL_RETURNS = "Retail Returns"
    CONSIGNER = "Consigner / Third Party"
    ESTATE = "Estate"


class EstRetailRange(str, Enum):
    UNDER_20 = "Under $20"
    FROM_20_TO_50 = "$20 - $50"
    FROM_50_TO_100 = "$50 - $100"
    FROM_100_TO_500 = "$100 - $500"
    ABOVE_500 = "Above $500"



class SortOption(str, Enum):
    # OUR friendly names; query_mapping.py maps them to Algolia replica indexes.
    ENDING_SOONEST = "ending_soonest"
    ENDING_LATEST = "ending_latest"
    PRICE_LOW_HIGH = "price_low_high"
    PRICE_HIGH_LOW = "price_high_low"
    RETAIL_LOW_HIGH = "retail_low_high"
    RETAIL_HIGH_LOW = "retail_high_low"
    BID_COUNT_LOW_HIGH = "bid_count_low_high"
    BID_COUNT_HIGH_LOW = "bid_count_high_low"


# --------------------------------------------------------------------------- #
# Core listing
# --------------------------------------------------------------------------- #
class Listing(BaseModel):
    """A single auction lot.

    current_bid / est_retail / bid_count stay None when the listing comes from
    Algolia search (no live pricing there); detail_source fills them later.
    """

    id: str
    # Optional: Algolia occasionally returns a hit with no "Lead Description".
    # Kept nullable so one untitled lot can't fail-validate the whole search.
    title: str | None = None
    url: str
    image_url: str | None = None

    # Live pricing — populated from the cargo API, not Algolia.
    current_bid: Decimal | None = None
    est_retail: Decimal | None = None
    bid_count: int | None = None

    # Facet / metadata fields from Algolia.
    category: str | None = None
    brand: str | None = None
    star_rating: float | None = None
    location: str | None = None
    event_type: EventType | None = None
    event_name: str | None = None
    closes_at: datetime | None = None  # derived from Algolia "Time Remaining"

    # Per-item condition flags from Algolia.
    item_condition: str | None = None  # e.g. "New", "Used"
    is_damaged: bool | None = None
    is_functional: bool | None = None
    missing_parts: bool | None = None
    in_package: bool | None = None
    assembly_required: bool | None = None

    fetched_at: datetime | None = None  # when we read the volatile fields


# --------------------------------------------------------------------------- #
# Search input / output
# --------------------------------------------------------------------------- #
class SearchFilters(BaseModel):
    """Internal representation of a search request, built inside the
    search_listings tool from its flat scalar parameters."""

    category: str | list[str] | None = None
    brand: str | list[str] | None = None
    min_star_rating: int | None = Field(default=None, ge=1, le=5)
    est_retail_range: EstRetailRange | None = None
    location: str | list[str] | None = None
    event_name: str | list[str] | None = None
    event_type: EventType | list[EventType] | None = None
    sort_by: SortOption = SortOption.ENDING_SOONEST
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=20, ge=1, le=100)


class SearchResult(BaseModel):
    listings: list[Listing]
    total_count: int | None = None
    page: int
    has_more: bool
    source: str = "algolia"
    warnings: list[str] = Field(default_factory=list)


class Pricing(BaseModel):
    """Live pricing for one listing, scraped from its product page.

    Algolia search carries none of this; ``detail_source`` fills it in from the
    product page HTML when a listing needs to be evaluated.
    """

    current_bid: Decimal | None = None
    est_retail: Decimal | None = None
    bid_count: int | None = None
    market_status: str | None = None  # "open" while biddable; else closed/ended


class DealEvaluation(BaseModel):
    """The result of scoring one listing as a deal.

    Pure data — computed by ``evaluate.evaluate()`` and returned to the assistant.
    Carries every intermediate number plus a verdict and human-readable notes so
    the assistant can *explain* the deal, not just print a score.
    """

    listing_id: str
    listing_url: str
    title: str | None = None

    # inputs used
    current_bid: Decimal | None = None
    est_retail: Decimal | None = None
    buyer_premium_rate: float
    tax_rate: float

    # computed
    effective_cost: Decimal | None = None      # what you'd actually pay: bid + premium + tax
    star_factor: float                          # condition multiplier from star rating (1.0 = 5-star)
    benchmark_cost: Decimal | None = None       # est_retail * RETAIL_HAIRCUT * star_factor:
                                                #   what a COMPARABLE lot typically sells for, all-in.
                                                #   This is the reference point, not a value estimate.
    deal_score: float | None = None             # 1 - (effective_cost / benchmark_cost)
                                                #   0 = typical price, >0 = cheaper than typical
    estimated_savings: Decimal | None = None    # benchmark_cost - effective_cost, in dollars

    # Forward-looking bidding guidance. Unlike deal_score (which compares the
    # CURRENT price to a FINAL-price benchmark, and so is only meaningful near
    # close), these are well-posed at any point in the auction.
    suggested_max_bid: Decimal | None = None    # bid ceiling to still get a good deal
    win_probability_at_max_bid: float | None = None   # P(closes at/below that ceiling)
    win_probability_at_current: float | None = None   # P(closes at/below the current bid)
    predicted_final_bid: Decimal | None = None  # median expected winning bid

    verdict: str                                # great_deal / good_deal / fair / overpriced /
                                                #   too_early (price not converged) / unknown
    notes: list[str] = Field(default_factory=list)


# --------------------------------------------------------------------------- #
# Authenticated account data — active bids / watchlist
# --------------------------------------------------------------------------- #
class AccountListing(BaseModel):
    """One lot from the user's authenticated Nellis dashboard.

    Nellis serves active bids and watchlist from the SAME endpoint — this model
    carries the `is_watching` / `has_participated` flags so account_source.py can
    filter one fetched list into both get_active_bids() and get_watchlist(),
    rather than these being two separate requests.
    """

    id: str
    title: str | None = None   # same nullability as Listing.title
    url: str
    image_url: str | None = None

    current_bid: Decimal | None = None
    next_bid: Decimal | None = None            # what you'd need to bid to lead
    est_retail: Decimal | None = None
    bid_count: int | None = None
    bidder_count: int | None = None
    watchlist_count: int | None = None

    location: str | None = None
    closes_at: datetime | None = None
    market_status: str | None = None

    item_condition: str | None = None          # e.g. "New", "Used"
    star_rating: float | None = None
    is_damaged: bool | None = None
    is_functional: bool | None = None
    missing_parts: bool | None = None
    in_package: bool | None = None
    assembly_required: bool | None = None

    is_watching: bool
    has_participated: bool                      # you've placed at least one bid
    is_winning: bool
    is_allowed_to_bid: bool
