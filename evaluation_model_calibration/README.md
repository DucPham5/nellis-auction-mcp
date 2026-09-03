# Calibrating `evaluate_deal`

**Status as of 2026-08-31: clean slate.** Three nights of scripts and data were
deleted after the work became hard to follow — too many things tested on too
little data, findings written up and then retracted, no agreed plan before
building. This file now holds only what's still true: the problem, the method,
and the lessons that earned their place. Nothing below cites a specific number
from the deleted data, since that data is gone and the numbers can't be
checked anymore. See `PROGRESS.md` at the project root for the full dated
history of what happened, including the retractions.

## The problem this solves

`evaluate_deal` scores a lot by comparing its price to Nellis's own "Estimated
Retail Price." The trouble: Nellis is a liquidation/returns site, so nearly
every lot sells for well under its retail estimate — that's just normal here,
not a sign of a good deal. Comparing to retail with no other reference point
means almost everything scores as a "great deal," which makes the score
useless. (A real example: an ordinary Blu-ray player scored 0.95/"great_deal"
at $13 against a $352 retail estimate — not because it was a steal, but
because *everything* looks like that on this site.)

To fix it, the formula needs to know what **normal** looks like here — the
typical price something actually sells for, as a fraction of its retail
estimate. Once that's known, a lot priced *at* the typical fraction scores
zero (an ordinary outcome), and only something meaningfully below it scores as
a real deal.

The only way to learn that number is from **lots that already sold**, with
their real final prices.

## Why this is harder than "just query the database"

Nellis's search index (Algolia) removes a lot within hours of it closing. You
cannot ask it for "the last 1,000 lots that sold" — that history simply isn't
there to query. The only closed lots visible for more than a few hours are
stale index records from months ago, and their product pages are dead by then.

But — and this is the piece that makes calibration possible at all — a lot's
**product page** (`nellisauction.com/p/x/<id>`) keeps working for a while
after the lot disappears from search, since the URL is just built from the
id. Verified directly in the deleted work: lots that had already vanished
from Algolia still returned correct final prices from their pages, matching
what had been recorded hours earlier (the price is settled, not still
moving). Verified window was at least ~12 hours; pages do eventually die, so
the practical rule is **collect within about a day of close, not longer**.

## The two-pass design

Since you can't retroactively query "what sold," you have to plan ahead:

1. **Pass 1 — while lots are still open**, pick a sample and write down their
   ids and metadata (retail estimate, condition, category, location, etc.).
   No prices yet — nothing has sold.
2. *(Time passes. The auctions actually run.)*
3. **Pass 2 — after those lots have closed**, visit each saved id's product
   page directly (bypassing search entirely, since we don't need it anymore)
   and read off the final price.

This is the same idea as writing down a list of houses currently for sale —
address, asking price — and coming back after they sell to see what each one
actually went for. This part of the design worked and is worth keeping.

## What we're confident in, going into the redesign

These directions held up across two independent nights before the reset —
worth carrying forward as working hypotheses, but the exact numbers behind
them are gone and would need re-deriving from fresh data, not assumed:

- **Nearly everything sells well below "Estimated Retail."** Comparing a bid
  to full retail is close to meaningless here — essentially every lot beats
  it, so beating it signals nothing.
- **Competition (how many bids a lot attracts) is a strong driver of final
  price** — plausibly the strongest one found. This held independently on two
  separate nights of data.
- **Item condition (New vs. Used, as Nellis labels it) does not predict
  price** — tested twice, no effect either time.
- **Star rating direction is real** (worse-rated lots sell for less), but the
  *size* of the effect moved a lot between nights, so treat the direction as
  trustworthy and any specific multiplier as unverified.

Two more directions were raised and looked promising, but were only ever
tested on a single night — meaning they have **zero replication** and should
be treated as open questions, not findings, until tested again:
- Whether recognizable brand names sell closer to retail than no-name goods.
- Whether a lot's bid count and time-remaining, observed mid-auction, predict
  where it will actually close.

## Hard-earned lessons on methodology (read before writing new scripts)

This is the part worth not re-learning the hard way:

- **A slice needs real sample size before you trust it.** A quick simulation
  showed that comparing two samples of ~50 lots each — even when drawn from
  the *identical* underlying distribution, i.e. a true difference of zero —
  produces an apparent 20%+ gap more than 40% of the time, purely from
  randomness. Several early "findings" (a price-tier effect, a location
  effect) looked convincing on one night and completely reversed on the next.
  Both were built on slices of only ~50 lots.
- **Don't trust a result until it replicates on an independent night.** A
  tight, statistically "significant" result *within one night* is not
  evidence it's real — it can still be pure noise, as the retracted findings
  above showed directly. Two nights minimum, ideally agreed on as a rule
  *before* collecting, not decided after seeing whether the numbers happen to
  agree.
- **Keep the sampling procedure identical across nights, deliberately.** An
  earlier inconsistency — sampling lots at different times before close on
  different nights — quietly changed the mix of what got sampled (cleaner
  inventory one night, messier the next) and manufactured an apparent
  night-to-night "trend" that was really just a procedural artifact once
  checked. Pin down sampling time/window as a fixed constant, not whatever
  hour a script happens to be run.
- **Decide what's being tested *before* collecting**, and keep the list
  short. Slicing the data by every available field after the fact (category,
  location, tier, condition flags, star rating, brand, bid count — in that
  order, as things came up) is how the noise problem above happened. A short,
  pre-committed list of variables, sized so each one gets enough data to
  actually resolve, is more likely to produce something trustworthy than
  testing everything and reporting whatever looked interesting.
- **Nights don't share lots.** Nellis's whole inventory turns over roughly
  every 24 hours, so there is no way to compare "the same item" across
  nights — every comparison is inherently a comparison of different,
  randomly-drawn items. This is exactly why sample size and replication
  matter so much here.

## The plan (agreed 2026-08-31, some pieces still open)

### Decided

**Output = "I'm looking at this lot — should I bid, and how much?"**
Predicted final price plus a suggested max bid. Not the discovery/ranking
problem ("find me good lots") — that's harder and can be built later on the
same foundation.

**Two-stage model.** Last time these were merged, which meant a bad prediction
couldn't be traced to a cause:

- *Stage 1 — what is this item worth?* Inputs known at listing time: retail
  price, star rating, brand. Output: "items like this typically close near $X."
- *Stage 2 — how is THIS auction going?* Inputs: stage 1's baseline plus bid
  count so far and hours remaining. Output: "but with 5 bids and 16h left,
  it's heading for $Y."

Splitting them means each half can be validated separately, and stage 1 works
alone for the discovery use case later.

**Note on which stage dominates:** stage 2 was initially framed as a small
"adjustment," but the earlier data suggested the opposite — bid count spanned
roughly a 4.5x range in outcomes while star rating spanned ~1.5x. Auction
dynamics look like the *main* driver and item attributes the correction on
top. Worth keeping in mind when allocating the data budget.

**Collect more per night.** Previous runs used ~500 population lots/night,
which was too few to support any slicing — and unnecessarily so, since at the
1 req/sec throttle ~2,000 lots is only ~35 minutes of background fetching.
Target ~1,500-2,000/night.

### Proposed, needs sign-off before building

**An unattended collector.** Stage 2 requires the same lots observed at
several points before close (~18h, ~6h, ~2h, ~30min). Last round this failed
for a mundane reason: the afternoon run simply never happened, leaving one
time-point. Proposal: a single script launched once in the morning that
samples the lots, wakes itself to snapshot them through the day, and fetches
final prices after close — removing the "did someone remember to run it"
failure mode.

**Pre-committed factor list** (deliberately short — the discipline that was
missing before):
- Stage 1: retail price, star rating, brand (recognizable yes/no)
- Stage 2: bid count, hours remaining
- *Excluded, not to be re-tested without a specific reason:* category,
  location, price tier (all tested and failed or reversed), New/Used (tested
  twice, no effect). Brand gets one honest shot — enough data to prove or kill
  it — rather than lingering as a maybe.

**Sample sizes implied:** stage 1 needs 3 star levels x 2 brand = 6 groups at
~400 each = ~2,400 lots. Stage 2 needs 5 bid levels x 4 time windows, but each
lot yields multiple observations. At ~1,500-2,000 lots/night that's roughly
**two nights**, not five.

**The bar, set before looking at data:** an effect enters the model only if it
holds on **two independent nights**, each with at least ~300 lots in the
relevant group.

### Decided 2026-08-31 (evening) — and built

- **Restart scope widened to "retest everything, no pre-exclusions."** Category,
  location, price tier and New/Used get a fresh test rather than inheriting a
  verdict from data that no longer exists. Re-excluding them would have been
  citing deleted evidence.
- **Brand = recognizable yes/no** (Option A). Known to be coarse; it tests
  whether brand carries *any* signal, not whether it's the right variable.
- **Star buckets ★≤3 / ★4 / ★5.** Finer levels are impossible: a whole evening
  window holds only ~28 ★1 lots.
- **launchd, not cron.** cron does not fire while a Mac is asleep and never
  backfills — the exact failure this design exists to survive.
- **Open question added to the test list:** does the bid-count effect *differ*
  by item type (branded vs not)? The two-stage model assumes one shared
  multiplier applies to every item; that assumption has never been tested, and
  if it's wrong the model is wrong for exactly the desirable items you'd most
  want to bid on.

## The collector (built 2026-08-31)

```
run_config.py    # all policy: window, strata sizes, brand list, round schedule
sampling.py      # Pass 1 — stratified draw of still-open lots
collector.py     # state machine: init / snap-now / tick / status
data/run_<date>/ # lots.jsonl, snapshots.jsonl, finals.jsonl, errors.jsonl, state.json
```

Imports `nellis.algolia`, `nellis.detail_source` and `nellis.http` read-only.
Nothing in `nellis/` was modified.

```bash
./.venv/bin/python3 -m evaluation_model_calibration.collector init --date YYYY-MM-DD --seed N
./.venv/bin/python3 -m evaluation_model_calibration.collector snap-now --label snap_t0
./.venv/bin/python3 -m evaluation_model_calibration.collector status
```

`tick` runs whichever round is due and exits; launchd calls it every 15 min
(`com.nellis.calibration.plist`). One round per tick, guarded by an flock so a
long finals round can't overlap the next tick and double our request rate.

### Two things the collector gets right that are easy to get wrong

**Pagination cap.** Algolia's sorted replica stops returning results around
record 10,000, but an evening window holds ~11.6k lots. Paging straight through
would silently drop the latest-closing ~13% — and late closers are not a random
subset. The window is therefore queried in hourly slices. Verified: the drawn
sample spans all six close-hours.

**Oversample contamination.** Targeted rows must never enter a population
statistic — this is what corrupted the previous round's haircut. Population is
deduped first and tagged `sample_source="population"`; every other stratum is
for within-group comparison only.

### Known confound, recorded before analysis

Low-star lots are much more expensive items (median est. retail **$83** at ★≤3
vs **$19** for population). Star rating and price are entangled in the
inventory itself, so any star effect must be checked within price bands before
it's believed.

### Still to do

1. **Write the pre-registration** — the fixed question list, a prediction for
   each, and the rule that anything noticed outside the list is exploratory.
   Must be written before any results are looked at.
2. Second night (2026-09-02) for replication.
3. Analysis — only after (1) and (2).
