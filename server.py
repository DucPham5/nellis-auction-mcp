"""Nellis Auction MCP server — search and evaluate auction listings."""

from mcp.server.fastmcp import FastMCP

from nellis import account_source, browse_source, detail_source, evaluate
from nellis.models import (
    AccountListing,
    DealEvaluation,
    EstRetailRange,
    EventType,
    Listing,
    SearchFilters,
    SearchResult,
    SortOption,
)

mcp = FastMCP("Nellis-Auction-Server")


@mcp.tool()
def search_listings(
    category: str | list[str] | None = None,
    brand: str | list[str] | None = None,
    min_star_rating: int | None = None,
    est_retail_range: EstRetailRange | None = None,
    location: str | list[str] | None = None,
    event_name: str | list[str] | None = None,
    event_type: EventType | list[EventType] | None = None,
    sort_by: SortOption = SortOption.ENDING_SOONEST,
    page: int = 1,
    page_size: int = 20,
) -> SearchResult:
    """Search Nellis Auction listings with optional filters.

    Each filter accepts either a single value or a list of values — a list is
    OR'd together (e.g. location=["SW Houston", "Katy"] matches either).

    Returns matching listings (without live pricing — use evaluate_deal for that),
    plus total_count and pagination info. Call again with an incremented `page`
    for more results.
    """
    filters = SearchFilters(
        category=category,
        brand=brand,
        min_star_rating=min_star_rating,
        est_retail_range=est_retail_range,
        location=location,
        event_name=event_name,
        event_type=event_type,
        sort_by=sort_by,
        page=page,
        page_size=page_size,
    )
    return browse_source.search(filters)


@mcp.tool()
def evaluate_deal(
    listing: Listing,
    buyer_premium_rate: float = evaluate.BUYER_PREMIUM_RATE,
    tax_rate: float = evaluate.DEFAULT_TAX_RATE,
) -> DealEvaluation:
    """Score a listing (from search_listings) as a deal, using its live pricing.

    Fetches the listing's current bid / retail / bid count from its product
    page, then scores it against the listing's condition flags. Returns the
    verdict plus every intermediate number so the assistant can explain it.
    """
    #1: fetch live pricing for this listing.
    #   pricing = detail_source.fetch_pricing(listing.id)

    pricing = detail_source.fetch_pricing(listing.id)

    # 2: run the pure scoring logic and return the result.
    #   return evaluate.evaluate(listing, pricing, buyer_premium_rate, tax_rate)
    return evaluate.evaluate(listing, pricing, buyer_premium_rate, tax_rate)


@mcp.tool()
def get_active_bids() -> list[AccountListing]:
    """Return lots from your Nellis account that you've placed at least one bid on.

    Authenticated — requires NELLIS_SESSION_COOKIE in .env. A lot can appear
    here and in get_watchlist() at the same time; the two are independent.
    """
    return account_source.get_active_bids()


@mcp.tool()
def get_watchlist() -> list[AccountListing]:
    """Return lots on your Nellis watchlist.

    Authenticated — requires NELLIS_SESSION_COOKIE in .env. Includes lots
    you're watching whether or not you've bid on them.
    """
    return account_source.get_watchlist()


if __name__ == "__main__":
    mcp.run()
