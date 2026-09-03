"""Pass 1 — draw the night's sample of still-open lots.

Reuses ``nellis.algolia.search()``, which accepts a raw ``{"index", "params"}``
dict. That lets us build close-time-window queries (which SearchFilters has no
field for) while still getting hit->Listing mapping, throttling, and error
translation for free — and without touching the MCP server's own code path.

Two non-obvious things this module exists to get right:

1. **Pagination cap.** Algolia's sorted replica stops returning results somewhere
   around record 10,000, but a full evening window holds ~11.6k lots. Paging
   straight through would silently drop the latest-closing lots — and late-closing
   lots are not a random subset (different auction events, different mix). So the
   window is queried in hourly slices, each far below the cap.

2. **Systematic, not first-N, sampling.** Taking the first N hits of a
   time-sorted query returns the earliest closers, not a random sample. We spread
   page picks evenly across each slice with a random start offset.
"""

from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone

from nellis import algolia
from nellis.config import ALGOLIA_INDEX
from nellis.models import Listing

from . import run_config as cfg

SORTED_INDEX = f"{ALGOLIA_INDEX}_time_remaining_asc"


def close_window(close_date: datetime) -> tuple[datetime, datetime]:
    """The UTC (start, end) of the evening close window for a given local date."""
    start = close_date.replace(
        hour=cfg.CLOSE_WINDOW_START[0], minute=cfg.CLOSE_WINDOW_START[1],
        second=0, microsecond=0, tzinfo=cfg.LOCAL_TZ,
    )
    end = close_date.replace(
        hour=cfg.CLOSE_WINDOW_END[0], minute=cfg.CLOSE_WINDOW_END[1],
        second=59, microsecond=0, tzinfo=cfg.LOCAL_TZ,
    )
    return start.astimezone(timezone.utc), end.astimezone(timezone.utc)


def _slices(start: datetime, end: datetime) -> list[tuple[int, int]]:
    """Split the window into hourly (lo_ts, hi_ts) unix-timestamp pairs."""
    out: list[tuple[int, int]] = []
    step = timedelta(hours=cfg.WINDOW_SLICE_HOURS)
    cur = start
    while cur < end:
        nxt = min(cur + step, end)
        out.append((int(cur.timestamp()), int(nxt.timestamp())))
        cur = nxt
    return out


def _request(
    lo: int,
    hi: int,
    *,
    page: int,
    page_size: int,
    extra_numeric: list[str] | None = None,
    facet_filters: list[list[str]] | None = None,
) -> dict:
    """Build one Algolia request for a time slice."""
    numeric = [f"Time Remaining > {lo}", f"Time Remaining < {hi}"]
    if extra_numeric:
        numeric.extend(extra_numeric)
    return {
        "index": SORTED_INDEX,
        "params": {
            "numericFilters": numeric,
            "facetFilters": facet_filters or [],
            "attributesToRetrieve": cfg.ATTRIBUTES,
            "hitsPerPage": page_size,
            "page": page,
        },
    }


def _sample_slice(
    lo: int,
    hi: int,
    want: int,
    *,
    page_size: int = 50,
    extra_numeric: list[str] | None = None,
    facet_filters: list[list[str]] | None = None,
    rng: random.Random,
) -> list[Listing]:
    """Systematically sample ``want`` lots from one time slice.

    Costs one cheap count query, then ceil(want/page_size) result pages spread
    evenly across the slice with a random start — so we're not always reading the
    same position in every slice.
    """
    probe = algolia.search(_request(lo, hi, page=0, page_size=1,
                                    extra_numeric=extra_numeric,
                                    facet_filters=facet_filters))
    total = probe.total_count or 0
    if total == 0:
        return []

    n_pages_needed = max(1, -(-want // page_size))          # ceil
    n_pages_avail = max(1, -(-total // page_size))
    n_pages_needed = min(n_pages_needed, n_pages_avail)

    # Even spacing with a random offset = systematic sampling.
    stride = n_pages_avail / n_pages_needed
    offset = rng.random() * stride
    page_indices = sorted({
        min(n_pages_avail - 1, int(offset + i * stride))
        for i in range(n_pages_needed)
    })

    out: list[Listing] = []
    for p in page_indices:
        result = algolia.search(_request(lo, hi, page=p, page_size=page_size,
                                         extra_numeric=extra_numeric,
                                         facet_filters=facet_filters))
        out.extend(result.listings)
        if len(out) >= want:
            break
    return out[:want]


def _spread_over_slices(
    slices: list[tuple[int, int]],
    total_want: int,
    *,
    rng: random.Random,
    extra_numeric: list[str] | None = None,
    facet_filters: list[list[str]] | None = None,
) -> list[Listing]:
    """Draw an equal share from each hourly slice."""
    per_slice = max(1, total_want // max(1, len(slices)))
    out: list[Listing] = []
    for lo, hi in slices:
        out.extend(_sample_slice(lo, hi, per_slice, extra_numeric=extra_numeric,
                                 facet_filters=facet_filters, rng=rng))
    return out


def draw_sample(close_date: datetime, *, seed: int | None = None) -> list[dict]:
    """Draw the full stratified sample for one night.

    Returns plain dicts (ready for JSONL) with a ``sample_source`` tag.

    Strata are deduped by id with priority population > star > brand. Population
    must stay an unbiased random draw of the window, so it always wins a tie —
    the targeted strata lose the ~10% overlap, which is immaterial to them since
    they're only ever used for WITHIN-group comparison, never for population
    statistics. Pooling targeted rows into a population median is exactly what
    corrupted the previous round.
    """
    rng = random.Random(seed)
    start, end = close_window(close_date)
    slices = _slices(start, end)

    tagged: dict[str, dict] = {}

    def add(listings: list[Listing], source: str) -> None:
        for lot in listings:
            if lot.id in tagged:
                continue            # first stratum to claim it keeps it
            row = lot.model_dump(mode="json")
            row["sample_source"] = source
            tagged[lot.id] = row

    # 1. Population — no filters. The only stratum valid for population stats.
    add(_spread_over_slices(slices, cfg.TARGET_POPULATION, rng=rng), "population")

    # 2. Star-targeted — lifts ★4 and ★<=3 toward the sample-size bar.
    per_stratum = cfg.TARGET_STAR // max(1, len(cfg.STAR_STRATA))
    for name, numeric in cfg.STAR_STRATA:
        add(_spread_over_slices(slices, per_stratum, rng=rng, extra_numeric=numeric), name)

    # 3. Brand-targeted — recognizable brands are individually tiny (single digits
    #    to low tens per window), so we take ALL of each rather than subsampling.
    #    No slicing here: each brand's result set is nowhere near the pagination
    #    cap, so one query over the whole window is both correct and 6x cheaper.
    whole_lo, whole_hi = int(start.timestamp()), int(end.timestamp())
    for brand in cfg.RECOGNIZABLE_BRANDS:
        result = algolia.search(
            _request(whole_lo, whole_hi, page=0, page_size=100,
                     facet_filters=[[f"Brand:{brand}"]])
        )
        add(result.listings, "brand_targeted")

    # 4. Location comparison — Houston/Dallas-area stratum. Made permanent
    #    2026-09-02 after running it manually on nights 1-2: population already
    #    covers the Vegas/NJ side naturally, but Houston/Dallas needs a direct
    #    pull (see PRE_REGISTRATION.md's Q7 note — deep pagination drifted over
    #    time on this index, which is what left these locations near-absent from
    #    a plain systematic draw in the first place; a single page-0 query
    #    sidesteps that instability entirely).
    add(_draw_location_comparison_listings(start, end), "location_houston_dallas")

    return list(tagged.values())


def _draw_location_comparison_listings(start: datetime, end: datetime) -> list[Listing]:
    """One direct query for the Houston/Dallas-area comparison stratum."""
    lo, hi = int(start.timestamp()), int(end.timestamp())
    facet_or = [f"Location Name:{loc}" for loc in cfg.LOCATION_COMPARE_LOCATIONS]
    result = algolia.search({
        "index": SORTED_INDEX,
        "params": {
            "numericFilters": [f"Time Remaining > {lo}", f"Time Remaining < {hi}"],
            "facetFilters": [facet_or],
            "attributesToRetrieve": cfg.ATTRIBUTES,
            "hitsPerPage": cfg.LOCATION_COMPARE_TARGET,
            "page": 0,
        },
    })
    return result.listings


def draw_location_comparison(close_date: datetime, existing_ids: set[str]) -> list[dict]:
    """Backfill the Houston/Dallas stratum into an already-sampled run.

    Used by `collector.py`'s `add-location-compare` command for runs sampled
    before this stratum became a permanent part of `draw_sample()` above.
    """
    start, end = close_window(close_date)
    rows: list[dict] = []
    for lot in _draw_location_comparison_listings(start, end):
        if lot.id in existing_ids:
            continue
        row = lot.model_dump(mode="json")
        row["sample_source"] = "location_houston_dallas"
        rows.append(row)
    return rows
