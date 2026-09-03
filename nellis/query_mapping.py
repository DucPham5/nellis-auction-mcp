"""Translate our SearchFilters into an Algolia request.

This is the ONE place Algolia's internal names live (facet keys, replica index
names). Everything else uses our friendly labels; here we convert them.

All the Algolia specifics below were confirmed via a live probe of the
`nellisauction-prd` index (see the learning log / CLAUDE.md).

This is a SKELETON — fill in the TODO gaps.
"""

from __future__ import annotations
import time
from enum import Enum
from .config import ALGOLIA_INDEX as INDEX_BASE
from .models import EstRetailRange, SearchFilters, SortOption

# Our SortOption -> Algolia virtual-replica index name.
# Confirmed replica names (query the name inside the parens):
#   _current_price_asc/_desc, _retail_price_asc/_desc,
#   _bids_asc/_desc, _time_remaining_asc/_desc
SORT_TO_INDEX: dict[SortOption, str] = {
    SortOption.ENDING_SOONEST: f"{INDEX_BASE}_time_remaining_asc",
    SortOption.ENDING_LATEST: f"{INDEX_BASE}_time_remaining_desc",
    SortOption.PRICE_LOW_HIGH: f"{INDEX_BASE}_current_price_asc",
    SortOption.PRICE_HIGH_LOW: f"{INDEX_BASE}_current_price_desc",
    SortOption.RETAIL_LOW_HIGH: f"{INDEX_BASE}_retail_price_asc",
    SortOption.RETAIL_HIGH_LOW: f"{INDEX_BASE}_retail_price_desc",
    SortOption.BID_COUNT_LOW_HIGH: f"{INDEX_BASE}_bids_asc",
    SortOption.BID_COUNT_HIGH_LOW: f"{INDEX_BASE}_bids_desc"
}

# Our SearchFilters field name -> Algolia facet key, for EQUALITY filters.
# (These become facetFilters like "Taxonomy Level 1:Electronics".)
FACET_KEYS: dict[str, str] = {
    "category": "Taxonomy Level 1",
    "brand": "Brand",
    "location": "Location Name",
    "event_name": "Auction Event Name",
    "event_type": "Auction Event Type"
}

# Our EstRetailRange -> Algolia numericFilters on the raw "Suggested Retail" price.
# (The site's buckets are really numeric ranges under the hood.)
RETAIL_RANGE_FILTERS: dict[EstRetailRange, list[str]] = {
    EstRetailRange.UNDER_20: ["Suggested Retail < 20"],
    EstRetailRange.FROM_20_TO_50: ["Suggested Retail >= 20", "Suggested Retail <= 50"],
    EstRetailRange.FROM_50_TO_100: ["Suggested Retail >= 50", "Suggested Retail <= 100"],
    EstRetailRange.FROM_100_TO_500: ["Suggested Retail >= 100", "Suggested Retail <= 500"],
    EstRetailRange.ABOVE_500: ["Suggested Retail > 500"]
}


def to_algolia_request(filters: SearchFilters) -> dict:
    """Build the Algolia query body + index name from our filters.

    Returns a dict like:
      {
        "index": "<replica index name>",
        "params": {
            "facetFilters": [["Taxonomy Level 1:Electronics"], ...],  # AND of ORs
            "numericFilters": ["Star Rating>=4", "Suggested Retail < 20"],
            "hitsPerPage": <page_size>,
            "page": <0-based page>,
        },
      }
    """
    facet_filters: list[list[str]] = []
    # "Time Remaining" is really the lot's close timestamp, not a countdown —
    # Algolia's index isn't purged of closed lots, so we always exclude anything
    # that has already closed (product page 404s otherwise; see evaluate_deal).
    numeric_filters: list[str] = [f"Time Remaining > {int(time.time())}"]

    # equality facets — for each field in FACET_KEYS, if the filter has a
    #   value (a single value or a list of values), append one facet_filters
    #   entry: [f"{facet_key}:{v}", ...] — multiple values here are OR'd
    #   together by Algolia (e.g. location="A" or location="B").
    #   (enum values need .value to get the text)
    for field_name, facet_key in FACET_KEYS.items():
        value = getattr(filters, field_name)
        if value is not None:
            values = value if isinstance(value, list) else [value]
            values = [v.value if isinstance(v, Enum) else v for v in values]
            facet_filters.append([f"{facet_key}:{v}" for v in values])

    # min_star_rating -> a numeric filter (only if it's set)
    if filters.min_star_rating is not None:
        numeric_filters.append(f"Star Rating >={filters.min_star_rating}")

    # est_retail_range -> numeric range filter(s) (only if it's set)
    if filters.est_retail_range is not None:
        numeric_filters.extend(RETAIL_RANGE_FILTERS[filters.est_retail_range])

    index = SORT_TO_INDEX[filters.sort_by]

    return {
        "index": index,
        "params": {
            "facetFilters": facet_filters,
            "numericFilters": numeric_filters,
            "attributesToRetrieve": [
                "objectID",
                "Lead Description",
                "Location Name",
                "Time Remaining",
                "Item Condition",
                "Is Damaged",
                "Is Functional",
                "Missing Parts",
                "In Package",
                "Assembly Required",
                "Brand",
                "Taxonomy Level 1",
                "Star Rating",
                "Suggested Retail",
                "Auction Event Name",
                "Auction Event Type",
        ],
            "hitsPerPage": filters.page_size,
            "page": filters.page - 1,
        },
    }
