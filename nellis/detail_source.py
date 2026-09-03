"""Fetch live pricing for one listing by scraping its product page.

Algolia search has no live pricing (current bid / retail $ / bid count). The cargo
REST API that holds it is gated to internal use (403 for outside clients, and the
real frontend doesn't call it for this either — see PROGRESS.md), so we get the
same data from the PUBLIC product page HTML, where Nellis embeds it in a
`turbo-stream` payload (see turbo_stream.py). robots.txt allows ``/p/`` and no
auth is needed.

This is HTML scraping — more fragile than an API — so we extract defensively and
raise ``NellisParseError`` if the expected fields aren't present.
"""

from __future__ import annotations

from decimal import Decimal

from . import http, turbo_stream
from .errors import NellisParseError
from .models import Pricing


def _product_url(product_id: str) -> str:
    """Public product page URL. Nellis routes by id, so the slug can be a placeholder."""
    return f"https://www.nellisauction.com/p/x/{product_id}"


def fetch_pricing(product_id: str) -> Pricing:
    """Scrape a listing's product page and return its live Pricing."""
    url = _product_url(product_id)
    response = http.request("GET", url)

    envelope = turbo_stream.decode(response.text)
    product = turbo_stream.find_product(envelope)

    # sanity check: the decoded object must actually be THIS listing, not some
    # other product embedded on the same page — a wrong number is worse than
    # an error (see CLAUDE.md conventions).
    if str(product.get("id")) != str(product_id):
        raise NellisParseError(
            f"Decoded product id {product.get('id')!r} did not match requested "
            f"id {product_id!r} ({url}) — page structure changed."
        )

    current = product.get("currentPrice")
    retail = product.get("retailPrice")
    bids = product.get("bidCount")
    status = product.get("marketStatus")

    if current is None and retail is None:
        raise NellisParseError(
            f"No pricing found on the product page for id {product_id} ({url}) — "
            "the listing may not exist, or the page structure changed."
        )

    return Pricing(
        current_bid=Decimal(str(current)) if current is not None else None,
        est_retail=Decimal(str(retail)) if retail is not None else None,
        bid_count=int(bids) if bids is not None else None,
        market_status=status,
    )
