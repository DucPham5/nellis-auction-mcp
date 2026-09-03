# PROGRESS.md — Nellis Auction MCP: status & notes

Companion to [CLAUDE.md](CLAUDE.md), which stays the stable architecture reference. **This file is
where churn lives** — phase status, open TODOs, in-flight plans, dated snapshots. Expect it to change
most sessions; check here first for "where did we leave off."

## Status

- **`turbo_stream` truncation bug FOUND + FIXED (2026-08-31).** The payload
  extractor used a NON-GREEDY regex, `enqueue\("(\[.*?)"\)`, which stops at the first
  `")` in the payload. Product descriptions routinely contain a parenthetical ending in
  an inch mark — e.g. `(width 22"-36", min height 13.75")` — which embeds exactly that
  sequence and truncated the payload ~10KB in, surfacing later as
  `NellisParseError: Unterminated string`. Replaced with a scanner (`_js_string_body`)
  that walks to the first *unescaped* closing quote. Verified: 8/8 previously-failing
  lots recovered, 5/5 previously-working lots unaffected.
  **Why this mattered more than its 2.7% rate suggests:** the failure was *correlated*,
  not random. Items described by their dimensions are furniture, appliances and A/C
  units — the expensive end of the inventory. So `evaluate_deal` was failing
  disproportionately on big-ticket lots, and the calibration sample would have been
  silently biased toward cheap items. Found by investigating a 2.7% error rate in the
  first calibration snapshot round rather than writing it off as noise.
- **`detail_source.fetch_pricing` regression FOUND + FIXED (2026-08-28).** Between 2026-08-07 (last
  verified working) and today, Nellis changed how product pages embed loader data: from plain flat JSON
  (`"currentPrice":41`) to Remix's `turbo-stream` format (`window.__remixContext.streamController.enqueue`),
  which flattens every value into one array and represents objects as index-reference maps rather than
  nested key:value pairs. The old regex matched nothing on ANY lot, open or closed — `evaluate_deal` was
  fully broken, not just degraded. Ruled out cargo REST API and SSE as alternatives first (see DEFERRED
  section below for the full investigation) — confirmed via the real client JS bundles that the actual
  Nellis frontend doesn't call cargo for this at all, and SSE only pushes bid-change events, never an
  initial price. Wrote a real turbo-stream decoder — [`turbo_stream.py`](nellis/turbo_stream.py) — that
  resolves the reference-map format properly instead of regexing for adjacent text, since adjacency turned
  out to be an unreliable artifact of serialization order, not a format guarantee. `detail_source.py` now
  walks `loaderData` to find the route carrying a `product` object, and sanity-checks the decoded `id`
  matches the requested product id (catches "grabbed the wrong listing's data" outright instead of
  returning a wrong number). Validated against independent ground truth: a closed lot's own
  `<meta description>` read "Sold for $2 | Retail: $726.24" and the decoder produced
  `current_bid=2, est_retail=726.24` exactly; a fresh 5-lot open sample all correctly decoded
  `market_status='open'`. This also incidentally proved out a key assumption for the calibration work
  below (closed lots stay scrapeable well past close — one test lot decoded correctly ~20+ minutes after
  closing).
- **Phase 1 (foundation): DONE + verified** — `errors.py`, `models.py`, `http.py`.
- **Phase 2 (search): DONE + verified** in the MCP Inspector — `query_mapping.py`, `algolia.py`,
  `browse_source.py`, `search_listings`.
- **Phase 3 (evaluate): DONE + verified end-to-end** — `detail_source.py`, `evaluate.py`,
  `evaluate_deal`. Scored a real listing correctly through the Inspector. **But see the DEFERRED
  section below** — the formula runs correctly and is still not a useful signal.
- **Phase 4 (authenticated bids/watchlist): DONE + verified** — `account_source.py`, `get_active_bids`,
  `get_watchlist`. Endpoint found via a logged-in DevTools capture (2026-08-07); verified both as a
  direct script call and through the MCP Inspector (`mcp dev server.py`, launched via `uv` — installed
  via `brew install uv` since it wasn't already present).
- Phase 5 (polish): `.gitignore` DONE; `README`, full Inspector pass still open.

### Open items
- Rotate the Nellis session cookie (log out/in) since the raw value passed through chat during capture —
  cheap and already-planned hygiene, not an active problem.
- `account_source.py`'s 401/403 → `NellisAuthError` handling is a best guess for what an expired cookie
  looks like from that endpoint; unconfirmed since the cookie was fresh during testing. Revisit if a
  future expiry actually surfaces as something else (e.g. a redirect or a 200 with empty data).
- Rework the `evaluate_deal` formula — **IN PROGRESS 2026-08-29**, see the calibration section below and [calibration/README.md](calibration/README.md).
- ~~Confirm the real **buyer's premium** rate from an invoice.~~ **CONFIRMED 15% (2026-08-29)** — product
  pages display "Buyers Premium 15%" directly in the Item Details block; verified on two lots at different
  locations. (An actual invoice could still add separate handling/lot fees; the *premium rate* is right.)
- ~~**BUG: `search_listings` can crash on a null title.**~~ **FIXED 2026-08-30** — `Listing.title` and
  `AccountListing.title` are now `str | None`. Algolia returns hits with no `Lead Description`, which
  was raising a pydantic ValidationError inside `_hit_to_listing` and taking down the entire search
  call over one untitled lot. Verified against the exact hit shape that crashed.
- Sort ties: many lots share one close timestamp, so ordering within a tie differs from the website's.
  Cosmetic; the result *set* is correct. Left alone deliberately.
- Tax default for `evaluate_deal`: user is in **Houston, TX** → default `tax_rate = 0.0825`.

Known Algolia facet values for `Location Name` (counts as of 2026-08): North Las Vegas, Delran, Phoenix,
Katy, Dean Martin, **SW Houston**, Mesa, Legacy Bids, Nellis Outlet, Denton, Dallas, Denver, Wild Finds
Henderson, Palatial Ave Estate, Swans Chance Ave Estate, Desert Woods Dr Estate. (There is no plain
"Houston" — the Houston-area ones are **SW Houston** and **Katy**.)

## `evaluate_deal` calibration — nights 1-2 DONE, night 3 collecting (2026-08-31)

Methodology and full reasoning: [calibration/README.md](calibration/README.md). Numbers below are results.

### Night 1 results (Sat 2026-08-29, 1,140 lots priced of 1,181 sampled)

**`RETAIL_HAIRCUT` = 0.182** (95% CI [0.153, 0.205]) — median ALL-IN cost (bid x1.15 premium x1.0825 tax)
as a fraction of est_retail. Raw median bid/retail = 0.146. On a $100-retail item: typical bid $14.60,
typical out-the-door $18.18. Only 3/480 lots closed at or above est_retail.

**The earlier 0.396 estimate was WRONG — sampling bias, now explained.** That 44-lot sample was pulled
from lots still lingering in Algolia after close, which turn out to be the heavily-contested ones:
57% of it had 11+ bids and 0% had zero bids, vs 11% and 20% in the proper sample. Lesson: don't sample
from the residue of closed lots; sample open lots and wait (the two-pass design).

**Applying 0.182 to night-1 lots:** verdicts go from 423 great / 45 good / 7 fair / 5 overpriced
(current broken behavior, 88% "great") to 127 / 81 / 32 / 240. Median score +0.818 -> 0.000.

### What actually predicts price

- **COMPETITION is the single biggest factor**, bigger than any item attribute: <=2 bids -> 0.081,
  >=6 bids -> 0.317 (**3.9x**). And **55% of all lots close with <=2 bids** — most inventory sells
  nearly unwatched.
- **Star rating IS Nellis's condition grade, not an independent signal.** ★5 lots are 0% damaged /
  0% non-functional; ★1 lots are 100% both. There are ZERO clean lots below ★4. **Do not apply a star
  penalty AND the individual flag penalties — that double-counts the same defect.** Recommend star
  rating as the single condition variable. Within clean lots only: ★4 = 0.112 vs ★5 = 0.156.
  Buckets, not levels: ★1/★2/★3 CIs overlap heavily and are not separable; ≤3 / 4 / 5 are.
- **`item_condition` (New/Used) has NO effect** — 0.147 vs 0.146. An earlier note called it "the
  strongest real signal" off the 44-lot sample; that did not survive real data. The condition
  information lives in star rating instead.
- **Category is real but under-powered.** Survives controlling for star rating (★5-only: Home
  Improvement 0.101 CI[0.069,0.120] vs Home & Household 0.173 CI[0.149,0.202] — cleanly separated).
  But only 2-3 categories have tight enough CIs to use; most span 2-4x. Not enough for a 15-row table yet.
- **Price tier**: night 1 showed $100-500 lots at 0.274 vs ~0.12-0.15 for cheaper tiers. **SUPERSEDED —
  night 2 got 0.103 for that same tier. See the night-2 retraction below; treat tier as UNPROVEN.**
  **>$500 tier still has almost no data.**
- **Location**: SW Houston 0.101 vs Katy 0.202 — CIs don't overlap, and it survives controlling for star
  rating. But it's concentrated in under-$20 lots (SW Houston 32% zero-bid vs Katy 14%); in the $20-50
  tier they nearly converge (0.136 vs 0.161). Not a clean per-market multiplier.

### The spread is competition, not noise — and it's the opportunity

Overall IQR 0.069-0.283 (width 0.214), but **within** bid-count buckets it collapses: 0 bids -> width
0.069, 1-2 bids -> 0.107, rising to 0.255 at 11+ bids. So low-attention lots are BOTH cheaper AND more
predictable. Percentiles: 5th 0.026 / 25th 0.069 / 50th 0.146 / 75th 0.283 / 90th 0.433.

**Design implication:** "this lot is being overlooked" is a more valuable signal than "this is cheap vs
retail". Promote bid activity from a side-note to a central input.

### FORMULA IMPLEMENTED (2026-08-30) — `evaluate.py` now calibrated

- `RETAIL_HAIRCUT = 0.193`, `STAR_FACTORS = {5:1.000, 4:0.687, 1-3:0.342}`, `DEFAULT_STAR_FACTOR = 1.0`.
- **Haircut was solved, not read off a group median**: `median(all_in_ratio / star_factor)` over the
  population sample, so the median lot scores exactly 0. Reading the POOLED ★5 median instead gives
  0.145 and lands the median at **-0.073** — the pool is contaminated by the targeted oversample, which
  deliberately over-collected out-of-package ★5 lots (those sell cheaper). Unbiased population ★5
  median is 0.158. Watch for this whenever mixing targeted rows into a population statistic.
- **Individual condition flags are NOT applied as penalties** — they're restatements of the star
  rating (see above), so charging for both double-counts. They're surfaced as notes instead.
  `in_package` was the one flag with plausible independent signal (0.74x within ★5) but its CIs
  **overlap** ([0.097,0.143] vs [0.140,0.168]) so it was deliberately left out pending night 2.
- Model changes: `DealEvaluation.adjusted_retail` -> **`benchmark_cost`** (it's a typical-cost
  reference, not a value estimate), `condition_factor` -> **`star_factor`**, added
  **`estimated_savings`** in dollars.
- Verified: replaying the 480 real closed lots gives median `deal_score` **-0.0008** and
  great 129 / good 80 / fair 31 / overpriced 240 (was 423/45/7/5 at median +0.818).

### KNOWN LIMITATION — the score is only valid NEAR CLOSE

The benchmark is calibrated on **final** prices, but mid-auction you observe a price that hasn't
converged. Measured directly: scoring 479 real open lots ~19h before close gives median
`deal_score` **+1.000** and **86% "great_deal"** — i.e. the exact "everything looks amazing" failure
the haircut was built to fix, arriving by a different route.

Interim guard (implemented): `_maturity_notes()` computes hours-to-close and, beyond
`MATURE_HOURS = 2.0`, replaces the verdict with **`too_early`** plus an explicit warning, rather than
returning a confident label on an unconverged price.

**PARTIAL FIX SHIPPED (2026-08-30): forward-looking bidding guidance.** Rather than only warning that
the score is premature, `evaluate_deal` now answers a question that IS well-posed at any point in the
auction: *what are the odds this closes at or below $X?* New `WIN_PROB_CURVE` holds the empirical
distribution of the **normalized** final bid (`final_bid / (est_retail x star_factor)` — dividing out
star_factor removes item quality, leaving the pure auction-outcome spread). New outputs:
`predicted_final_bid`, `suggested_max_bid` (ceiling that still hits `TARGET_DEAL_SCORE = 0.20`),
`win_probability_at_max_bid`, `win_probability_at_current`.

Why this matters: a lot sitting at $2 with 14h left is not cheap, it just hasn't started. The tool now
says so directly — one live example predicted a $25.52 close with only a **1% chance** of ending at the
current $2.

**Validated out-of-sample.** Checking the curve against the 480 rows it was built from is circular and
trivially perfect — ignore that. The real check used the **660 targeted rows, which were NOT used to
build the curve** and deliberately oversample damaged/low-star lots, so `star_factor` has to carry the
adjustment: claimed 10/25/50/75/90% vs actual 10.6/29.5/55.9/77.9/87.6% — all within ~6 points.

**KNOWN FLAW (found 2026-08-30, deferred pending more data): one global curve does not fit all price
levels.** `WIN_PROB_CURVE` assumes the final-price-as-fraction-of-retail distribution is the same shape
regardless of how expensive the item is. Measured on ★5 population lots, it is not:

| tier | n | p25 | p50 | p90 |
|---|---|---|---|---|
| <$20 | 220 | 0.081 | 0.153 | 0.462 |
| $20-50 | 118 | 0.042 | 0.142 | 0.385 |
| $50-100 | 54 | 0.043 | 0.157 | 0.514 |
| **$100-500** | 46 | **0.188** | **0.281** | 0.559 |

Two distinct problems:
1. **Expensive lots close much higher** — $100-500 lots land at roughly 2x the ratio of cheaper ones
   (p50 0.281 vs ~0.15). The single blended curve is therefore too optimistic for them.
2. **The $1 bid floor makes cheap lots structurally different.** Bidding starts at $1, so a $10-retail
   lot can never go below ratio 0.100, while a $500 lot can reach 0.002. The curve happily reports
   "10% of lots close below 0.040", which is an *impossible* outcome for a sub-$25 item. Worse, cheap
   lots pile up ON the floor: **60% of sub-$20 lots closed at exactly $1**, vs 0% of $100-500 lots. So
   that tier isn't a smooth distribution at all — it's a spike at $1 plus a thin tail, and "is this a
   deal?" degenerates into "will anyone else notice it?".

Practical effect: guidance is sound in the mid-range, over-optimistic for $100-500 lots, and close to
meaningless for very cheap lots already sitting at $1.

**Fix (deferred):** per-tier curves plus explicit handling for lots at the $1 floor. Held off
deliberately — the $100-500 tier has only 46 lots right now, and splitting 480 lots four ways would
trade one bias for a worse one. Revisit once night 2 roughly doubles the population sample.

**Remaining work still needs the snapshot data** — the curve above is UNCONDITIONAL: it ignores how
far along a lot already is. A lot with 8 bids at 1h out will not close at the 10th percentile, but the
curve doesn't know that. Condition it on (bid_count, hours_to_close)
instead of final-only, so it can answer "0 bids at 19h out typically ends at X". That is exactly what
`calibrate_snapshot.py` collects.

### BIGGEST OPEN QUESTION: is `est_retail` credibility the real variable? (2026-08-30)

A user counter-example broke the model and may reframe the whole thing. **Logitech MX Master 4**
(`125662413`, $119.69 retail, ★4): user reports it closes at **$65-80 every time**. That's ratio
0.54-0.67 — **past the 99th percentile** of our entire measured distribution, and *consistently*, not
as an outlier. The tool predicted **$12.76** and suggested a **$10.20 max bid**: advice that would make
you drop out of something worth ~$70.

Two separate failures exposed:
1. **The curve is unconditional** — the lot already had 5 bids in <4h with 16h left, and the prediction
   ignored that entirely. Conditioning on night-1's bid-count buckets implies ~$42, still low but far
   closer. (This is the known snapshot fix.)
2. **NEW — no concept of brand / real market value.** Nothing in the model distinguishes a Logitech
   mouse from a no-name gadget at the same retail and star rating.

**Hypothesis worth taking seriously:** the ~0.15 median ratio is not "the Nellis discount" — it is
largely a measure of **how inflated `est_retail` is**. Most inventory is no-name product with an
invented MSRP, so it closes at a small fraction of a fictional number. Real brands have honest retail
prices and genuine resale demand, so they close near retail. If true, the ratio is partly measuring
*retail-price credibility*, not deal quality — which would reframe the whole model.

Weak night-1 support (only **6** exact brand matches, so hypothesis NOT finding):
LEGO 0.715 · DEWALT 0.633 · Apple 0.591 · Ninja 0.559, vs no-name median 0.146. The user's Logitech
data point lands squarely in that branded cluster. (An earlier version of this check used substring
matching and wrongly counted "POIKSHARK" as Shark and "Leviton" as Levi's — redone with exact matching.)

**Test set up:** new `calibration/calibrate_pass1c_brands.py`. `Brand` IS an Algolia facet (unlike the
condition flags), so it samples server-side. Added **793 lots across 54 recognizable brands** to
tonight's batch, tagged `sample_source="targeted_brand"` — including 20 Logitech lots, so the exact
counter-example gets measured. Note branded lots skew expensive (median retail $68 vs ~$21 population),
which entangles this with the price-tier flaw above; analysis must control for tier.

### Night 2 in progress (Sun 2026-08-30)

Two open questions night 1 can't answer:
1. **Day-of-week stability** — all night-1 data is a single Saturday. Unknown whether weekdays differ.
2. **Mid-auction prediction** — we only ever measured bid count AT CLOSE. When actually bidding you see
   the count *so far, with time left*. Night 1 literally cannot answer "1 bid, 4 hours out -> where does
   this land?"

New `calibration/calibrate_snapshot.py` fixes #2: run it before close to record each lot's current
bid/bid_count plus `hours_to_close`, repeatedly if desired. Pair with pass-2 finals to build
"N bids at T hours out -> final ratio".

Data files are now date-stamped (`pending_<date>.json`, `final_<date>.json`, `snapshots_<date>.json`)
so nights don't overwrite each other. Night 2: 500 lots sampled, closing 2026-08-30 18:00-23:59.
First snapshot taken ~01:45 (T-16h to T-22h). **TODO: second snapshot mid-afternoon (closer to close),
then pass 2 after midnight.**

## NIGHT 2 RESULTS (Sun 2026-08-30) — 1,267 lots priced

**Day-of-week: STABLE, no proven effect.** Sat median ratio 0.1461 CI[0.124,0.165] vs Sun 0.1206
CI[0.104,0.140] — CIs overlap. **Combined haircut over both nights (n=959): 0.181** (currently shipped:
0.193 — worth updating). Caveat: Sat+Sun are both WEEKEND. A weekday has never been tested.

**RETRACTION — the price-tier effect was noise.** Night 1 showed $100-500 lots at 0.274 and I recorded
it as a real finding. Night 2 shows **0.103** for the same tier — a complete reversal. At ~45 lots per
tier per night this was never separable from noise. The tier conclusion has now flipped three times
(44-lot trial: no effect / night 1: effect / night 2: no effect). Treat as UNPROVEN, and do not build
per-tier curves on this evidence. (The $1-floor argument for cheap lots still stands on its own — that
one is structural, not statistical.)

**BRAND EFFECT: CONFIRMED, and large.** Controlled within matched price tiers (so it is not the tier
effect in disguise), branded vs population:

| tier | population | branded | effect |
|---|---|---|---|
| <$20 | 0.142 | 0.214 | 1.51x |
| $20-50 | 0.092 | 0.221 | 2.41x |
| $50-100 | 0.139 | 0.301 | 2.17x |
| $100-500 | 0.100 | 0.349 | 3.49x |

Every tier's CIs are SEPARATED, and the effect grows with price. Per-brand (★5 only): LEGO 0.637,
ASUS 0.511, Google 0.511, Stanley 0.509, DEWALT 0.499 — versus a 0.121 population median.

**But BRAND is too coarse a variable — the real one is product-level desirability.** Logitech's own
median is only **0.122**, barely above population. Within Logitech: MX Master 4 **0.418** (21 bids),
mid-range keyboard combos 0.24-0.29, cheap combos 0.16, a USB dongle 0.141. Same brand, 3x spread,
because "Logitech" spans $14 dongles and $120 premium mice. A brand flag alone would have rated that
dongle highly and been wrong — same failure mode as the night-1 Black+Decker battery (★5, undamaged,
still closed at 0.050 because a bare spare battery has almost no buyer pool).

On the user's counter-example specifically: the MX Master 4 closed at **$50 (ratio 0.418)**, below the
$65-80 they reported seeing, but still ~4x the model's $12.76 prediction and the top Logitech lot of
the night.

**SNAPSHOTS: the strongest predictor found so far.** Bid count at ~19h before close vs final ratio:

| bids at 19h out | final ratio | 95% CI | n |
|---|---|---|---|
| 0 | 0.087 | [0.073, 0.091] | 311 |
| 1 | 0.216 | [0.177, 0.240] | 111 |
| 2-3 | 0.327 | [0.240, 0.392] | 40 |
| 4-6 | 0.400 | [0.245, 0.473] | 14 |

A 5x spread with **tighter CIs than any other variable measured**. Price climb after the 19h mark:
1 bid -> 5.0x, 2-3 bids -> 3.7x, 4-6 bids -> 2.5x. This is precisely what the unconditional
`WIN_PROB_CURVE` is missing — it treats the observed price as informative when it is systematically
2.5-5x low.

### METHODOLOGY FLAW — night 1 is not cleanly comparable to nights 2-3

The sampling *time and window* were not held constant, which I did not notice until auditing:

| night | sampled at | window before close | ★5 share | defect rate |
|---|---|---|---|---|
| 1 | 15:45 | 2-24h | **91%** | 9% |
| 2 | 01:30 | 10-26h | 84% | 15% |
| 3 | 01:30 | 10-26h | 82% | 18% |

Sampling 2h before close vs 17h before catches a different slice of the batch, and night 1's sample came
out systematically cleaner. Since ★5 lots sell higher, that inflates night 1's median on its own:
**36% of the night1->night2 "drift" disappears** once star rating is held constant (raw gap 0.0254 ->
★5-only gap 0.0163). So part of what I attributed to day-of-week was my own inconsistency.

**Nights 2 and 3 used identical procedure**, so that pair IS a clean comparison — and it's the one that
tests brand and snapshots. **Fix going forward: pin the sampling time/window as a constant** rather than
whatever hour the script happens to be run.

### CLAIM AUDIT (what actually replicates across nights 1-2, population lots only)

Prompted by justified user skepticism after several claims were made off single nights.

**Holds on both nights:**
- Everything sells below retail (0.6% / 0.0% at-or-above) — the core premise, rock solid.
- Competition drives price: 0 bids 0.069/0.063, 3-5 bids 0.186/0.197, 6-10 0.281/0.224, 11+ 0.350/0.334.
- New vs Used: no effect, both nights.
- Star direction (★4 < ★5) holds, but **magnitude is unstable** — ratio 0.42 (n1) vs 0.71 (n2). The
  shipped `STAR_FACTORS[4] = 0.687` matches night 2 and not night 1.

**RETRACTED — looked convincing on one night, reversed on the next:**
- **Price tier.** $100-500: 0.274 (n1) vs 0.103 (n2). Only ~46-49 lots per tier per night. Flipped three
  times across three samples. No proven effect. (The $1-floor argument for cheap lots is arithmetic, not
  statistics, and still stands.)
- **Location.** Claimed SW Houston 0.101 vs Katy 0.202 with *separated CIs*; night 2 gives Katy **0.091**.
  Reversed. **Tight CIs within a single night proved nothing about replication** — the key lesson here.

**NOT VALIDATED — single night only, zero replication:**
- **Brand effect (1.5-3.5x).** Night 2 only; night 1 had no branded sample.
- **Snapshot / early-bid-count prediction.** Night 2 only.
These are the two findings that were presented most confidently and are the least tested.

**Also note:** lots do NOT persist between nights (0 id overlap — inventory turns over completely every
~24h). So day-of-week, inventory mix, and sampling noise are inherently confounded and cannot be
separated by this design. "Night-to-night variation" is the most that can honestly be claimed.

### Night 3 in progress (Mon 2026-08-31)### Night 3 in progress (Mon 2026-08-31) — targets the remaining gaps
- **First WEEKDAY test** — nights 1-2 were both weekend.
- **Multiple snapshot rounds** — night 2 only got one (the mid-afternoon run never happened), so we
  can't yet see how odds shift as a lot approaches close. Need a near-close observation (1-3h out) to
  build a properly time-conditioned model.
- Sample: 500 population + 375 branded (`PER_BRAND` cut 20->8, since the brand effect is established;
  effort redirected to snapshot coverage). Closes 2026-08-31 18:00-23:58.

### Next code changes (supported by data, not yet applied)
1. `RETAIL_HAIRCUT` 0.193 -> **0.181** (two-night combined).
2. Remove the price-tier claim from docs — unproven.
3. **Condition `WIN_PROB_CURVE` on bid count** — the biggest accuracy win available.
4. Brand/desirability as an input — effect is real and large, but needs a product-level signal, not a
   brand whitelist.

## `evaluate_deal` formula reference + known gaps

The formula works end-to-end and the math is correct, but it is **not yet useful as a signal**. Revisit
before trusting any verdict. Current formula:

```
effective_cost   = current_bid × (1 + 0.15 premium) × (1 + 0.0825 tax)
condition_factor = 1.0 − (damaged .20 + missing_parts .25 + non_functional .40
                          + not_in_package .05 + event_type .00–.05)   [floored at 0.10]
adjusted_retail  = est_retail × condition_factor
deal_score       = 1 − (effective_cost / adjusted_retail)
verdict          = ≥.50 great · ≥.20 good · ≥.00 fair · else overpriced
```

**Core problem — no discrimination.** Nearly all lots are returned/overstock Amazon goods that close
*below* `est_retail`, so "beats est_retail" is the baseline, not a signal. Everything scores positive
and gets labeled `great_deal`. Observed live: a wholly ordinary lot (Sony Blu-ray, $13 bid on $352.79
retail, 18h left) scored **0.95 → "great_deal"**.

**Known gaps in the current formula:**
- `item_condition` ("New"/"Used") is **completely ignored** by `_condition_factor` — the Used Sony above
  got `condition_factor = 1.0`. Most obvious value signal we have.
- `star_rating` is fetched and filterable but never enters the math.
- Penalties are **subtractive**, so they over-punish stacking (damaged+missing+non-functional+unpackaged
  = .90 off → hits the 0.10 floor). Multiplicative is more principled. −40% for non-functional is likely
  far too lenient.
- **No pickup/shipping cost.** These are pickup-based auctions; on a $13 item the drive or a shipping fee
  can exceed the item cost.
- **No time-remaining / bid-count risk.** A lot with 18h left and 1 bid will climb; the score reads as if
  you win at today's price. Decided approach: keep `deal_score` a pure "is this price good *right now*"
  signal and surface `hours_remaining` + `bid_count` + a plain-language note, rather than folding time
  decay into the score (which would conflate "cheap" with "likely to stay cheap" and be hard to tune).
- **Idea, not yet acted on (2026-08-28): `bidder_count` / bid velocity as a competitiveness signal**,
  same "surface as a note, don't fold into the score" treatment as time-remaining above. `bidHistory` on
  the product page (`{time, name, amount, type}` per bid — `turbo_stream.py` now resolves it) gives
  distinct-bidder count and bid timing, e.g. "5 bids from 2 people" (a duel) reads very differently from
  "5 bids from 5 people" (broad interest) even at identical `bid_count`. Checked whether a stronger
  *leading* signal — watcher count — is available too: confirmed NOT exposed publicly, checked both the
  raw page payload and the rendered UI on a live lot. `watchlistCount` exists in Nellis's system (already
  in this codebase as `AccountListing.watchlist_count`) but only via the authenticated dashboard, scoped
  to lots you're already watching/bidding — no way to get it for an arbitrary lot. `bidder_count` is the
  best available proxy (lagging — already-committed bidders only — not leading like watcher count would
  be, but it's what's actually obtainable).
- `BUYER_PREMIUM_RATE = 0.15` — **rate confirmed** (product pages display it directly; see Open items above). Still open: whether tax should apply to the premium too (we currently tax bid+premium).
- `if pricing.bid_count in (None, 0)` conflates "zero bids" with "we failed to parse it".

Condition-penalty weights stay placeholders until the calibration data exists — tune with data, not
opinion.
