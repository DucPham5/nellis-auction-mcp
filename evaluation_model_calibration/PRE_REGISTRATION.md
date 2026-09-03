# Pre-registration — `evaluate_deal` rebuild

**Written 2026-08-31, ~21:15 local — before any lot in the sample had closed.**
Night 1 (`run_2026-09-01`) was drawn at ~20:50 and its lots close Tuesday
18:00–23:59. At the time of writing, zero outcome data exists: no final price,
for any lot, has been observed. That is the point of the timing.

## Why this document exists

The previous round of this work collapsed not because the data was bad but
because the questions kept changing after the data arrived. Category, location,
price tier, condition flags, brand and bid count were each tested in the order
they happened to come up, and whatever looked striking got written up. Price
tier reversed three times across three samples. Location produced textbook-tight
confidence intervals one night and the opposite result the next.

That is the predictable outcome of testing many things on noisy data, not bad
luck. A simulation run at the time showed that two samples of ~50 lots drawn
from an *identical* distribution — a true difference of exactly zero — show an
apparent 20%+ gap more than 40% of the time.

Writing the questions and predictions down first is what makes a surprise
meaningful. If a prediction below turns out wrong, that is informative. Without
the prediction on record, "the data said something" and "I found something" are
indistinguishable.

## Rules of engagement

1. **The question list below is fixed.** Anything else noticed in the data is
   labelled *exploratory* and requires its own confirmation round before it can
   be called a finding or enter the model.
2. **Population statistics come only from `sample_source == "population"`.**
   Targeted strata (`star_4`, `star_low`, `brand_targeted`) exist to make
   within-group comparisons possible and must never be pooled into a population
   median. Pooling them is what corrupted the previous haircut estimate.
3. **The bar:** an effect enters the model only if it holds **in the same
   direction on two independent nights**, with **≥300 lots in the relevant
   group each night**.
4. **A group under 300 is reported as "insufficient data" — never as "no
   effect."** Absence of evidence gets said out loud.
5. **Medians with bootstrap confidence intervals**, not means. The ratio
   distribution is heavily right-skewed and a mean is not a useful summary of it.

## Outcome variables

```
final_ratio   = final_bid / est_retail
all_in_ratio  = final_bid x 1.15 (buyer's premium) x 1.0825 (Houston tax) / est_retail
```

`final_ratio` is the primary variable for comparing groups. `all_in_ratio` is
what the model's benchmark is ultimately calibrated on, since it's what you
actually pay.

## Stage 1 — item attributes

| # | Question | Prediction | How it's tested |
|---|---|---|---|
| 1 | Do lots sell below est. retail? | **Yes, overwhelmingly.** Median `final_ratio` well under 0.5; under 2% of lots close at or above retail. | Population only |
| 2 | Does star rating predict price? | **Direction yes** (★5 > ★4 > ★≤3), magnitude unknown and expected to be unstable between nights | Must be checked *within price bands* — see confound below |
| 3 | Do the individual condition flags add anything **beyond** star rating? | **No.** They restate the same defect; charging for both double-counts. | Within a fixed star level, do damaged / missing-parts / non-functional / in-package separate? |
| 4 | New vs Used | **No effect.** Tested twice before, no effect either time. | Population only |
| 5 | Recognizable brand vs not | **Recognizable brands close at a higher fraction of retail.** Direction predicted; magnitude not. *(User: confident yes.)* | `brand_targeted` vs population, matched on price band |
| 6 | Category | **Mostly won't separate.** Expect at most 2–3 categories with non-overlapping CIs. | Only categories with ≥300 lots/night are tested at all |
| 7 | Location | **No reliable effect.** It reversed completely last round. | Population only, ≥300/location |

**Limitation found 2026-08-31, revised after investigation the same night:**
tonight's population sample contains only 3 locations — `Delran`, `Legacy Bids`,
`Wild Finds Henderson` — missing `SW Houston`, `Katy`, `Denton`, `Dallas`, which
DO have substantial lots (1,000+ each) in the same close window. Root cause,
confirmed live: Algolia's paginated ordering for an identical query is **not
stable over time** on this index. Re-querying the exact page numbers used at
sample time (~21:57 CDT) several hours later returned a materially different
mix, including Katy and SW Houston — even though 47/50 of those lot ids already
existed at sample time. Likely mechanism: many lots share one `Time Remaining`
value (already known — see CLAUDE.md), and as new lots keep streaming into the
index through the evening, the tie-break order among same-timestamp lots
shifts, so "page 69" doesn't mean the same thing at two different times.

**This is a live-index instability, not a bug in the sampling arithmetic** —
confirmed by directly checking the actual page indices computed by tonight's
run: all four returned full, valid, non-empty results, just a different mix of
locations than what got recorded at draw time.

**Decision (2026-08-31, revised twice the same night):** first decided not to
fix the gap, since location was already the prediction expected to be null and
bid activity (Q9/Q10) is the bigger priority. Reversed a few minutes later —
added a dedicated `location_houston_dallas` stratum (400 lots: Katy 155,
SW Houston 121, Denton 67, Dallas 57) instead, since a direct one-night
comparison was cheap and purely additive (didn't touch the systematic draw,
`snap_06` onward, or anything already collected).

**Q7's comparison for night 1 is now: Delran + Wild Finds Henderson
(Vegas/NJ, 725 lots, already in population) vs. Katy + SW Houston + Denton +
Dallas (Houston/Dallas area, 400 lots, new stratum).**

**Update 2026-09-02: made permanent.** Manually backfilled for night 2 (393
lots) at the time. Once it became clear this stratum would keep getting added
by hand every night, it was folded directly into `sampling.py`'s `draw_sample`
as a standing step — night 3 (387 lots) is the first to get it automatically,
with a one-time manual backfill for the sample already drawn before the code
changed. Now genuinely multi-night data toward the two-night bar, not a
one-off test.
| 8 | Retail price level | **No monotonic effect on ratio.** Flipped three times across three samples. | Continuous, not buckets — bucketing is part of why it flip-flopped |

**Note on Q8:** the `$1` bid floor is a separate, *arithmetic* claim that needs
no statistical test — a $10-retail lot cannot close below ratio 0.10 because
bidding starts at $1, while a $500 lot can reach 0.002. Cheap lots therefore
pile up on the floor. This is structural and stands regardless of what Q8 finds.

## Stage 2 — auction dynamics

| # | Question | Prediction | How it's tested |
|---|---|---|---|
| 9 | Does bid count observed mid-auction predict the final ratio? | **Yes — strongly. Expected to be the single best predictor**, stronger than any item attribute. | Snapshot rounds vs finals |
| 10 | Does the *timing* of that observation matter? | **Yes.** The same bid count means different things at 18h out vs 2h out; early observations should climb more. | Bucket by (bid_count x hours_to_close) |
| 11 | **Does the bid-count effect differ between branded and non-branded lots?** | **Yes — it differs by item.** *(User prediction, stated 2026-08-31 before any close. My own prior was genuinely neutral.)* | Q9's analysis, split by brand |

**Q11 is the most consequential question on this list.** The two-stage model
assumes one shared multiplier: that "3 bids at 6h out means 1.8x typical" holds
equally for a LEGO set and a no-name gadget. The user — who actually bids on
this site — predicts confidently that it does not.

If they're right, the model needs an **interaction term**, not just two
independent stages, and the current design is wrong precisely for the desirable
items you'd most want to bid on. That is the failure the Logitech MX Master 4
counter-example already exposed once: the model predicted $12.76 on a lot that
closed at $50.

Recorded explicitly so the outcome is informative either way:
- **Difference found** → the two-stage split needs an interaction term. Expected.
- **No difference found** → a genuine surprise that contradicts direct
  experience of the site, and worth investigating as a possible *measurement*
  failure (e.g. the coarse brand flag failing to capture desirability at all —
  which Q5 would help distinguish) before being accepted as a real null.

## Confound recorded in advance

**Star rating and price are entangled in the inventory itself.** Measured on
night 1's sample *before* any outcome data existed: median est. retail is **$83**
for ★≤3 lots versus **$19** for population. Damaged goods skew toward expensive
items — cheap broken things are presumably not worth listing. Any star effect
must therefore be checked within price bands, or it will partly be a price
effect wearing a star costume.

## What is deliberately not being asked

- Product-level desirability (beyond the coarse brand flag). Known to matter —
  Logitech spans a $120 mouse and a $14 dongle — but there is no product-level
  signal available yet, and inventing one mid-round is how the last round lost
  its discipline.
- Watcher counts. Confirmed not exposed publicly for arbitrary lots.
- Anything requiring the authenticated account endpoints. Out of scope by design.
