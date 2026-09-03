"""The search seam.

server.py's search_listings tool calls into here. This module is the single
place that knows *how* search is backed (Algolia). Keeping it separate means the
tool layer never imports Algolia details directly.

This is a SKELETON — fill in the TODO gap.
"""

from __future__ import annotations

from . import algolia, query_mapping
from .models import SearchFilters, SearchResult


def search(filters: SearchFilters) -> SearchResult:
    """Run a search: translate filters -> Algolia request -> SearchResult."""

    request = query_mapping.to_algolia_request(filters)
    return algolia.search(request)

