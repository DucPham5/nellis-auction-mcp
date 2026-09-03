"""Score a listing as a deal — pure math, no network.

Takes a Listing (condition + metadata from search) and a Pricing (live numbers
from detail_source) and returns a DealEvaluation. Easy to unit-test: hand it
made-up Listing/Pricing objects and check the output.

THE CORE IDEA (and why this isn't just "compare to retail"):
Nellis is a liquidation/returns site, so essentially everything sells far below
its "Estimated Retail Price" — in a 1,140-lot sample only 3 lots closed at or
above est_retail, and the median lot went for 14.6% of it. Comparing a bid to
full retail therefore flags ~88% of lots as "great deals", which is useless.

So we compare against what a COMPARABLE lot actually sells for instead:

    benchmark_cost = est_retail x RETAIL_HAIRCUT x star_factor
    deal_score     = 1 - (effective_cost / benchmark_cost)

deal_score = 0 now means "you're paying the typical price for this kind of lot",
positive means cheaper than typical, negative means you're overpaying by Nellis
standards even if it still looks cheap against the sticker retail number.

All constants below are CALIBRATED FROM REAL DATA (see calibration/README.md and
PROGRESS.md), not guesses. Re-derive them with calibration/calibrate_analyze.py
if Nellis's pricing behaviour drifts.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from .models import DealEvaluation, Listing, Pricing

BUYER_PREMIUM_RATE = 0.15   # confirmed: product pages display "Buyers Premium 15%"
DEFAULT_TAX_RATE = 0.0825   # Houston, TX combined sales tax

# --------------------------------------------------------------------------- #
# Calibration constants — from 1,140 real closed lots (2026-08-29).
# --------------------------------------------------------------------------- #

# Median ALL-IN cost (bid x premium x tax) as a fraction of est_retail, for a
# 5-star lot. Solved so the median lot in the population sample scores exactly
# 0.000 -- i.e. median(all_in_ratio / star_factor) -- rather than read off a
# group median directly. That distinction matters: reading the POOLED 5-star
# median gives 0.145, but that pool is contaminated by the targeted oversample
# (which deliberately over-collected out-of-package 5-star lots, and those sell
# cheaper). The unbiased population 5-star median is 0.158.
# 95% CI on the population median ratio: [0.153, 0.205].
# NOTE: derived from a single Saturday night. Day-of-week variation is UNTESTED.
RETAIL_HAIRCUT = 0.193

# Star rating is Nellis's own condition grade, and it is the condition variable
# that matters. Verified in the sample: 5-star lots are 0% damaged / 0%
# non-functional, 1-star lots are 100% both, and there are ZERO clean lots below
# 4 stars. So the individual damage/missing-parts/non-functional flags are
# largely RESTATEMENTS of the star rating — applying both would double-count the
# same defect. Factors are relative to a 5-star lot.
#
# 1/2/3 stars are collapsed into one bucket on purpose: their confidence
# intervals overlap heavily and they are not statistically separable.
STAR_FACTORS: dict[int, float] = {
    5: 1.000,
    4: 0.687,
    3: 0.342,
    2: 0.342,
    1: 0.342,
}
# ~90% of inventory is 5-star, so an unknown rating is treated as 5-star rather
# than penalised. This is deliberately optimistic: it will never invent a
# discount that isn't there, so an unknown-rating lot can't be flagged a bargain
# purely because we lack data about it.
DEFAULT_STAR_FACTOR = 1.000

# Empirical distribution of the NORMALIZED final bid ratio:
#     final_bid / (est_retail x star_factor)
# Dividing out star_factor removes item quality, leaving the pure "how did this
# auction go" spread. From 480 real closed lots (2026-08-29).
#
# This is what makes forward-looking guidance possible. Comparing a CURRENT price
# to a FINAL-price benchmark is apples-to-oranges (see MATURE_HOURS above), but
# "what are the odds this closes at or below $X" is a well-posed question at any
# point in the auction, early or late.
WIN_PROB_CURVE: list[tuple[float, float]] = [
    (0.01, 0.0157), (0.05, 0.0273), (0.10, 0.0402), (0.20, 0.0627),
    (0.25, 0.0737), (0.30, 0.0946), (0.40, 0.1118), (0.50, 0.1552),
    (0.60, 0.1863), (0.70, 0.2522), (0.75, 0.2904), (0.80, 0.3210),
    (0.90, 0.4371), (0.95, 0.5529), (0.99, 0.8018),
]

# The deal quality a suggested max bid aims for. 0.20 == the "good_deal"
# threshold: pay at most ~80% of what a comparable lot typically costs.
TARGET_DEAL_SCORE = 0.20


def _median_normalized() -> float:
    """The p50 of WIN_PROB_CURVE — the typical normalized winning bid."""
    for p, v in WIN_PROB_CURVE:
        if p >= 0.50:
            return v
    return WIN_PROB_CURVE[-1][1]


def _win_probability(normalized_bid: float) -> float:
    """P(lot closes at or below this normalized bid), by interpolating the curve.

    `normalized_bid` is bid / (est_retail * star_factor), i.e. quality-adjusted.
    """
    lo_p, lo_v = 0.0, 0.0
    for p, v in WIN_PROB_CURVE:
        if normalized_bid <= v:
            if v == lo_v:
                return p
            frac = (normalized_bid - lo_v) / (v - lo_v)
            return lo_p + frac * (p - lo_p)
        lo_p, lo_v = p, v
    return 0.995  # above the 99th percentile — you'd essentially always win


# deal_score thresholds -> verdict. Higher score = better deal.
VERDICT_THRESHOLDS = [
    (0.50, "great_deal"),
    (0.20, "good_deal"),
    (0.00, "fair"),
]  # below 0.00 -> "overpriced"


def _d(x: float) -> Decimal:
    """Safely turn a float into a Decimal (via str, to avoid float imprecision)."""
    return Decimal(str(x))


def _star_factor(listing: Listing, notes: list[str]) -> float:
    """Condition multiplier from the star rating, with an explanatory note."""
    rating = listing.star_rating
    if rating is None:
        notes.append(
            "No star rating available — scored as if 5-star (most inventory is), "
            "so a poor-condition lot could look better here than it really is."
        )
        return DEFAULT_STAR_FACTOR

    key = max(1, min(5, int(round(rating))))
    factor = STAR_FACTORS[key]
    if key == 5:
        notes.append("5-star condition — the baseline this score is calibrated against.")
    else:
        label = "1-3 star" if key <= 3 else "4-star"
        notes.append(
            f"{label} condition — comparable lots sell for ~{factor:.0%} of what a "
            "5-star equivalent does, and the score already accounts for that."
        )
    return factor


def _condition_notes(listing: Listing, notes: list[str]) -> None:
    """Surface the raw condition flags as context.

    These are NOT applied as separate penalties: the calibration showed they're
    almost entirely encoded in the star rating already (see STAR_FACTORS), so
    charging for them again would double-count. They still matter to a human
    deciding whether they want the item at all, so we report them.
    """
    problems = []
    if listing.is_damaged:
        problems.append("damaged")
    if listing.missing_parts:
        problems.append("missing parts")
    if listing.is_functional is False:
        problems.append("not functional")
    if listing.in_package is False:
        problems.append("not in original package")
    if listing.assembly_required:
        problems.append("assembly required")
    if problems:
        notes.append("Condition flags: " + ", ".join(problems) + ".")


def _competition_notes(pricing: Pricing, notes: list[str]) -> None:
    """Flag how contested the lot is — the single biggest driver of final price.

    From the calibration sample: lots closing with <=2 bids went for ~8% of
    retail, lots with >=6 bids went for ~32% — a 3.9x gap, larger than any item
    attribute. 55% of all lots close with <=2 bids.

    This is deliberately NOT folded into deal_score. Bid count is a *consequence*
    of price, not a predictor of it, and a lot that is cheap right now because
    nobody has noticed it yet may not stay cheap. Conflating "cheap now" with
    "will stay cheap" would make the score mean two different things at once.
    """
    bids = pricing.bid_count
    if bids is None:
        notes.append("Bid count unavailable — can't judge how contested this lot is.")
    elif bids == 0:
        notes.append(
            "No bids yet. Lots that stay uncontested close very cheap (~7% of retail), "
            "but this one has the most room to climb if it gets noticed."
        )
    elif bids <= 2:
        notes.append(
            f"Only {bids} bid(s) — lightly contested so far. Most lots (55%) end here "
            "and close cheap, but the price can still move."
        )
    elif bids <= 5:
        notes.append(f"{bids} bids — moderately contested; expect the price to keep climbing.")
    else:
        notes.append(
            f"{bids} bids — heavily contested. These typically close around 32% of retail, "
            "roughly 4x what an overlooked lot goes for."
        )


# A lot's price has not converged until bidding actually happens near close.
# Measured: scoring open lots ~19h before close puts 86% in "great_deal" with a
# median score of +1.000, versus 26.9% / 0.000 on the same lots' FINAL prices.
# The benchmark is calibrated on final prices, so applying it early is
# systematically over-optimistic -- the same "everything looks great" failure the
# haircut was introduced to fix, just arriving by a different route.
MATURE_HOURS = 2.0   # within this long of close, the price is meaningfully settled


def _hours_to_close(listing: Listing) -> float | None:
    """Hours until this lot closes, or None if we don't know."""
    if not listing.closes_at:
        return None
    now = datetime.now(timezone.utc)
    closes = listing.closes_at
    if closes.tzinfo is None:
        closes = closes.replace(tzinfo=timezone.utc)
    return (closes - now).total_seconds() / 3600.0


def _maturity_notes(listing: Listing, pricing: Pricing, notes: list[str]) -> bool:
    """Warn when the score is being read too early to mean much.

    Returns True if the lot is 'mature' (close enough to closing that the score
    is trustworthy), False otherwise.
    """
    hours = _hours_to_close(listing)
    if hours is None:
        notes.append(
            "Close time unknown — can't tell whether this price has settled, so treat "
            "the score as provisional."
        )
        return False
    if hours <= 0:
        return True
    if hours <= MATURE_HOURS:
        notes.append(f"Closes in ~{hours:.1f}h — price is close to final, so this score is meaningful.")
        return True

    notes.append(
        f"*** READ THE SCORE WITH CAUTION: {hours:.1f}h left. *** Prices are set in the "
        "final stretch, so an early lot looks far cheaper than it will end up. Measured "
        "on real lots ~19h out, 86% scored 'great_deal' but only 27% deserved it at close. "
        "Re-check within ~2h of closing for a score you can act on."
    )
    return False


def _verdict(deal_score: float) -> str:
    """Map a deal_score to a verdict label using VERDICT_THRESHOLDS."""
    for threshold, label in VERDICT_THRESHOLDS:
        if deal_score >= threshold:
            return label
    return "overpriced"


def evaluate(
    listing: Listing,
    pricing: Pricing,
    buyer_premium_rate: float = BUYER_PREMIUM_RATE,
    tax_rate: float = DEFAULT_TAX_RATE,
) -> DealEvaluation:
    """Score one listing as a deal and return a DealEvaluation."""
    notes: list[str] = []
    current_bid = pricing.current_bid
    est_retail = pricing.est_retail

    star_factor = _star_factor(listing, notes)
    _condition_notes(listing, notes)

    # what you'd actually pay, all-in
    if current_bid is not None:
        effective_cost = current_bid * (1 + _d(buyer_premium_rate)) * (1 + _d(tax_rate))
    else:
        effective_cost = None

    # what a comparable lot typically costs, all-in
    if est_retail is not None:
        benchmark_cost = est_retail * _d(RETAIL_HAIRCUT) * _d(star_factor)
    else:
        benchmark_cost = None

    if effective_cost is not None and benchmark_cost and benchmark_cost > 0:
        deal_score = 1 - float(effective_cost / benchmark_cost)
        estimated_savings = benchmark_cost - effective_cost
        verdict = _verdict(deal_score)
    else:
        deal_score = None
        estimated_savings = None
        verdict = "unknown"
        notes.append("Missing current bid or retail — cannot score this deal.")

    # ---- forward-looking bidding guidance -------------------------------- #
    suggested_max_bid = None
    win_at_max = None
    win_at_current = None
    predicted_final_bid = None
    if est_retail is not None and est_retail > 0:
        cost_multiplier = (1 + _d(buyer_premium_rate)) * (1 + _d(tax_rate))
        quality_base = est_retail * _d(star_factor)   # what the curve is normalized against

        # median expected winning BID (not all-in) for a comparable lot
        predicted_final_bid = quality_base * _d(_median_normalized())

        # highest bid that still lands at TARGET_DEAL_SCORE, converted back
        # from all-in cost to a raw bid you'd actually type in
        target_cost = benchmark_cost * _d(1 - TARGET_DEAL_SCORE)
        suggested_max_bid = target_cost / cost_multiplier
        win_at_max = _win_probability(float(suggested_max_bid / quality_base))

        if current_bid is not None:
            win_at_current = _win_probability(float(current_bid / quality_base))

        notes.append(
            f"Bidding guidance: comparable lots typically close near "
            f"${predicted_final_bid:.2f}. Bidding above ${suggested_max_bid:.2f} means "
            f"paying more than a good deal; at that ceiling you'd win roughly "
            f"{win_at_max:.0%} of the time."
        )
        if win_at_current is not None and current_bid is not None:
            notes.append(
                f"At the current ${current_bid:.2f}, the odds this lot actually closes "
                f"that low are about {win_at_current:.0%}."
            )

    mature = _maturity_notes(listing, pricing, notes)
    if not mature and verdict != "unknown":
        # Don't hand back a confident label on a price that hasn't converged.
        verdict = "too_early"

    _competition_notes(pricing, notes)

    if pricing.market_status and pricing.market_status != "open":
        notes.append(f"Auction status is '{pricing.market_status}' — may no longer be biddable.")

    return DealEvaluation(
        listing_id=listing.id,
        listing_url=listing.url,
        title=listing.title,
        current_bid=current_bid,
        est_retail=est_retail,
        buyer_premium_rate=buyer_premium_rate,
        tax_rate=tax_rate,
        effective_cost=effective_cost,
        star_factor=star_factor,
        benchmark_cost=benchmark_cost,
        deal_score=deal_score,
        estimated_savings=estimated_savings,
        suggested_max_bid=suggested_max_bid,
        win_probability_at_max_bid=win_at_max,
        win_probability_at_current=win_at_current,
        predicted_final_bid=predicted_final_bid,
        verdict=verdict,
        notes=notes,
    )
