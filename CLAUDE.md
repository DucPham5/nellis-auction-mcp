# CLAUDE.md — Nellis Auction MCP

Personal MCP server for **Nellis Auction** (nellisauction.com, a liquidation/overstock auction site)
so an AI assistant can browse listings, evaluate whether lots are good deals, and read the user's own
active bids and watchlist. Nellis has **no official public API and no third-party OAuth**, so data comes
from the same backends the public website uses.

**This file is the stable reference** — scope, architecture, conventions, how auth works. It should
rarely need edits. Current phase status, open TODOs, and in-flight plans/decisions live in
[PROGRESS.md](PROGRESS.md) instead, which is expected to change every session — check there for
"where did we leave off."

## Scope — read-only, four tools

1. `search_listings(...)` — public listing search with the site's full filter set.
2. `evaluate_deal(...)` — deal-quality scoring using Nellis's own "Est. Retail" value.
3. `get_active_bids()` — the user's current bids (authenticated).
4. `get_watchlist()` — the user's watched lots (authenticated).

**Out of scope by design:** placing/cancelling bids or any account-modifying action. The authenticated
tools carry account-ban risk under Nellis's ToS ("unauthorized API access"), so keep them read-only and
low-volume. Never add a tool that mutates the account.

## Data sources (discovered via `window.ENV` on `/browse`, plus DevTools captures)

- **Algolia search API** — primary source for `search_listings`. Powers all browse/search/filter.
  - App ID `GL1QVP8R29`, public search key `d22f83c614aa8eda28fa9eadda0d07b9`, index `nellisauction-prd`.
  - `POST https://GL1QVP8R29-dsn.algolia.net/1/indexes/nellisauction-prd/query`
    (headers `X-Algolia-Application-Id`, `X-Algolia-API-Key`). ~198k records.
  - These are browser-public keys (search-only) — we use exactly what the site's own frontend uses.
  - **Facets** → our filters: `Brand`, `Star Rating`, `Location Name`, `Suggested Retail`,
    `Taxonomy Level 1`/`Taxonomy Level 2`, `Auction Event Name`, `Auction Event Type` (+ `Size`, `Color`).
  - **Per-hit fields**: `objectID` (product id), `Lead Description` (title), `Photo`, `Location Name`,
    `Time Remaining` (unix ts), and condition flags: `Item Condition`, `Is Damaged`, `Is Functional`,
    `Missing Parts`, `In Package`, `Assembly Required`.
  - **Not in Algolia hits**: live **current bid** and **bid count**. These are transactional data that
    live in the bidding backend, not the search index — `detail_source` fetches them per listing.
  - `Suggested Retail` **is** a real per-item dollar value (e.g. `112.83`), not just a bucket facet —
    it maps to `est_retail`. The `EstRetailRange` buckets are numeric range filters *over* this field.
  - **`attributesToRetrieve` is required.** Without it Algolia returns only Nellis's small default field
    set (title, photo, location, time, condition flags) — `Suggested Retail`, `Brand`, `Star Rating`,
    `Taxonomy Level 1`, and the auction-event fields all come back missing. `query_mapping.py` names
    every field explicitly; it is an allowlist, so new fields must be added there too.
  - **`Time Remaining` is an absolute close timestamp, not a countdown.** `query_mapping.py` always adds
    `Time Remaining > now` or searches return closed listings. Closed-lot behavior is asymmetric: a
    lot that just closed is **removed from the index within hours** (so there's no way to query "recently
    closed" after the fact — see `calibration/README.md` for how the calibration work works around this),
    but a batch of ~270-day-old orphaned records from 2025-12-01 never got purged and still linger, with
    dead product pages that 404. Don't rely on either recently-closed OR very-old-closed lots being queryable.
- **cargo REST API** `https://cargo.prd.nellis.run/api` — **abandoned.** The `/api/live-products` route
  exists but is backend-internal (403; rejects our `productId` format). Do not retry.
- **Product page HTML** `https://www.nellisauction.com/p/x/<id>` — **the live-pricing source.** Nellis
  embeds the page's data as a Remix `turbo-stream` payload (`window.__remixContext.streamController
  .enqueue("[...]")`), NOT plain JSON — every value lives in one flat array, and objects are
  reference-maps (`{"_K":V}`) pointing to other array slots rather than nested `key:value` pairs.
  `turbo_stream.py` decodes this properly (resolves the reference graph) and `detail_source.py` walks
  the decoded `loaderData` to the route carrying a `product` object, reading `currentPrice`, `retailPrice`,
  `bidCount`, `marketStatus` off it — plus `bidHistory` (per-bid `{time, name, amount, type}` records,
  giving distinct-bidder count). It sanity-checks the decoded `id` matches the requested one before
  trusting any field. Still scraping, so still fragile to a future format change — it raises
  `NellisParseError` when the expected structure is absent (which also correctly catches closed/removed
  lots). Nellis silently changed this format once already (flat JSON -> turbo-stream, sometime between
  2026-08-07 and 2026-08-28) with zero notice; expect it could happen again.
- **SSE** `https://sse.nellisauction.com/live-products?productId=<id>` — pushes bid updates only on
  change (heartbeat pings otherwise); not an initial-price source. Out of scope (future).
- **Dashboard active-auctions endpoint** (authenticated) —
  `https://www.nellisauction.com/dashboard/auctions/active?_data=routes%2Fdashboard.auctions.active`
  — another Remix loader, called directly. GET, auth via `Cookie: __session=<value>`. Returns
  `data.myAuctions.records`: every lot the user is watching and/or has bid on, each with a `userState`
  block (`isWatching`, `hasParticipated`, `isWinning`, `nextBid`, `isAllowedToBid`). **One endpoint
  serves both account tools** — `account_source.py` filters the same fetched list by `hasParticipated`
  (→ `get_active_bids`) vs `isWatching` (→ `get_watchlist`); Nellis doesn't expose them as separate
  requests.

robots.txt disallows `/` except `/`, `/p/`, `/browse`, `/sitemap*`, assets. Avoid `/search`. Algolia and
cargo are separate hosts, but treat them as the site's own backends — keep request volume polite.

## Architecture

`server.py` is a thin FastMCP entrypoint (tool definitions only). All logic lives in the `nellis/`
package. The tool layer only imports `models` and calls into the source modules, so the data-fetch
mechanism stays isolated behind one seam.

```
server.py               # FastMCP() + @mcp.tool() thin adapters
nellis/
  config.py             # env/.env loading: Algolia credentials (public defaults) + session_cookie()
  errors.py             # NellisError hierarchy (Network / Blocked / Parse / Auth)
  models.py             # pydantic models: Listing, SearchFilters, SearchResult, Pricing, DealEvaluation,
                         #   AccountListing, enums
  http.py               # shared httpx client: throttle, timeouts, UA, cookie, error translation
  query_mapping.py      # SearchFilters -> Algolia facetFilters/numericFilters + sort replica index
  algolia.py            # Algolia POST + map hits -> Listing
  browse_source.py      # search(filters) -> SearchResult (the search seam)
  turbo_stream.py       # decodes Remix's turbo-stream payload format (see Data sources above)
  detail_source.py      # live pricing for one product id, scraped from the product page via turbo_stream
  evaluate.py           # pure deal math, no network
  account_source.py     # get_active_bids() / get_watchlist() from the dashboard loader (needs session cookie)
```

**Multi-value filters:** every facet filter (`category`, `brand`, `location`, `event_name`,
`event_type`) accepts a single value *or* a list. `query_mapping.py` normalizes to a list and puts all
values in one `facetFilters` entry, which Algolia ORs; separate entries (different fields) are ANDed.

## Conventions

- **Money is `Decimal`, never `float`** (avoids rounding surprises).
- **All web requests go through `http.py`** (`http.request(...)`) — never call `httpx` directly from a
  source module. That centralizes throttling (~1 req/s), the User-Agent, timeouts, and error translation.
- **Raise our own errors** from source modules (`NellisNetworkError`, etc.); FastMCP wraps any exception
  as `ToolError(f"Error executing tool {name}: {e}")`, so make exception messages actionable and include
  the URL. `search_listings` may soften failures to an empty `SearchResult` + warnings; `evaluate_deal`
  should propagate (a wrong number is worse than an error).
- **Filters with a small fixed set are enums** (`EventType`, `EstRetailRange`, `SortOption`); dynamic
  facets (`category`, `brand`, `location`, `event_name`) stay free `str`.
- **Algolia's internal names are quarantined to `query_mapping.py`** — the rest of the code uses our
  friendly labels; use `.value` to get an enum's text when building the Algolia request.
- Tool functions are plain `def` (FastMCP runs them in a thread pool); models drive the auto-generated
  tool schemas, so field types matter.

## Auth (for the account tools)

Cookie import via `.env`. User logs into Nellis (via "Continue with Google"); Nellis sets a `__session`
cookie (a signed JWT bundle — treat it as a real credential, not an opaque ID). User copies **just its
value** into `NELLIS_SESSION_COOKIE` in a local `.env` (git-ignored via `.gitignore`, loaded by
`python-dotenv`). `config.session_cookie()` reads it; `account_source.py` sends it as
`Cookie: __session=<value>`. Missing → `NellisAuthError` before any request. Rejected by Nellis (401/403
on the dashboard endpoint) → `NellisAuthError` telling the user to re-log-in and refresh the cookie.

## Commands

```bash
# run anything with the project venv:
./.venv/bin/python3 <script>

# quick model/import check:
./.venv/bin/python3 -c "from nellis import models, http, errors; print('ok')"

# MCP Inspector (manual end-to-end testing of tools):
./.venv/bin/mcp dev server.py
```

`mcp dev` launches the server via `uv` under the hood (not the project's own venv directly) — needs
`uv` on PATH (`brew install uv`) or it fails with `spawn uv ENOENT`.

Verification is manual/layered (live third-party site, no CI against Nellis): pure-logic checks on
`evaluate.py` first, then a fetch-layer smoke test through `browse_source.search(...)`, then the MCP
Inspector.

## Tone for this project

The user is learning Python/MCP as we build. Explain concepts when filling in skeletons; prefer guiding
+ small verifications over dumping large finished files unprompted.
