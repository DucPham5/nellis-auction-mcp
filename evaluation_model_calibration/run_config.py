"""Run configuration for one night of calibration collection.

Everything tunable lives here so the collector itself stays mechanism, not policy.
A "run" is one evening's batch of lots: sampled while open, snapshotted through the
day, then re-read after close for final prices.

Times are LOCAL (America/Chicago) because that's how Nellis's close windows read to
a human; everything is converted to UTC before it touches Algolia.
"""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

LOCAL_TZ = ZoneInfo("America/Chicago")

DATA_DIR = Path(__file__).parent / "data"

# --------------------------------------------------------------------------- #
# The close window we sample from.
#
# Nellis runs its main batch in the evening. We pin this as a CONSTANT rather than
# "whatever is 12-26h away when the script happens to run" — an earlier round
# varied the sampling time between nights, which quietly changed the mix of lots
# caught (one night came out 91% five-star, the next 84%) and manufactured a
# night-to-night "trend" that was really just procedural drift.
# --------------------------------------------------------------------------- #
CLOSE_WINDOW_START = (18, 0)    # 18:00 local
CLOSE_WINDOW_END = (23, 59)     # 23:59 local

# Algolia's sorted replica caps pagination around 10k records, and a full evening
# window holds ~11.6k. Slicing the window into hourly sub-queries keeps every
# query well under the cap AND guarantees even coverage across close times —
# without this, the latest-closing lots are silently unreachable.
WINDOW_SLICE_HOURS = 1

# --------------------------------------------------------------------------- #
# Sample sizes per stratum.
#
# Stratified because random sampling cannot reach the agreed 300-lots-per-group
# bar for rare groups: in a typical window ★5 outnumbers ★1 by ~500x, and the
# single most common brand holds under 1% of lots.
# --------------------------------------------------------------------------- #
TARGET_POPULATION = 1200   # unfiltered — the ONLY set valid for population stats
TARGET_STAR = 600          # oversample of ★4 and ★<=3
TARGET_BRAND = 600         # oversample of recognizable brands

# Subset re-read at every snapshot round.
#
# Deliberately weighted toward the BRAND comparison rather than spread evenly
# across strata. Snapshots exist to answer Stage 2 (auction dynamics) and,
# critically, Q11 — "does the bid-count effect differ by item type?", which is
# the most consequential open question and the one the user predicts differs.
# Testing it means splitting branded lots across bid-count buckets, so ~150
# branded lots (an even 4-way split of 600) would leave ~30-50 per cell — the
# exact sample size that manufactured the previous round's phantom findings.
#
# Star rating and condition flags are Stage 1 questions, answered by FINAL
# prices alone. They need no snapshots, so they claim no snapshot budget.
SNAPSHOT_STRATA = ("brand_targeted", "population")
SNAPSHOT_PER_STRATUM = 707   # all branded lots, plus an equal population arm

# --------------------------------------------------------------------------- #
# Snapshot rounds — local clock times on the day lots close.
#
# Fixed wall-clock rounds are deliberate. Lots close anywhere from 18:00 to 23:59,
# so a single round yields a natural SPREAD of hours-to-close (a 21:00 round is
# 0h out for one lot and 3h out for another), covering more of the
# (bid_count x hours_left) grid than per-lot scheduling would.
#
# "pass1" is not listed here: sampling itself doubles as the earliest observation,
# which is why it should run the night before if possible (~20-26h out).
# --------------------------------------------------------------------------- #
SNAPSHOT_ROUNDS: list[tuple[str, tuple[int, int]]] = [
    ("snap_06", (6, 0)),
    ("snap_12", (12, 0)),
    ("snap_16", (16, 0)),
    ("snap_1830", (18, 30)),
    ("snap_21", (21, 0)),
]

# Final prices — the morning AFTER close. Product pages stay readable for at least
# ~12h past close, so this is comfortably inside the safe window.
FINALS_TIME = (1, 0)  # 01:00 local, day after close

# --------------------------------------------------------------------------- #
# Location comparison — added 2026-08-31, made a PERMANENT part of every
# night's draw on 2026-09-02 (see sampling.py's draw_sample, step 4). Population
# already covers the "Vegas/NJ" side naturally (Delran + Wild Finds Henderson);
# this targets the side that was structurally missing from a plain systematic
# draw (see PRE_REGISTRATION.md's Q7 note on the pagination-drift finding), so a
# direct comparison exists every night rather than needing a manual backfill.
# --------------------------------------------------------------------------- #
LOCATION_COMPARE_LOCATIONS = ["SW Houston", "Katy", "Dallas", "Denton"]
LOCATION_COMPARE_TARGET = 400

# --------------------------------------------------------------------------- #
# Auto-init — start the next night's run without being asked.
#
# AUTO_INIT_AT is PINNED, and that matters more than it looks. The single worst
# methodology error of the previous round was sampling at whatever hour the
# script happened to be run: one night at 15:45, the next at 01:30. That changed
# which slice of the batch got caught (one night came out 91% five-star, the next
# 84%) and produced an apparent night-to-night "trend" that was really just
# procedural drift. Night 1 here was sampled at ~20:50 local, so every subsequent
# night must sample at the same clock time to stay comparable.
#
# To stop collecting:
#   launchctl unload ~/Library/LaunchAgents/com.nellis.calibration.plist
# or set AUTO_INIT = False (existing runs still finish their pending rounds).
# --------------------------------------------------------------------------- #
AUTO_INIT = True
AUTO_INIT_AT = (20, 45)              # local, the evening BEFORE the close window
AUTO_INIT_GRACE = timedelta(minutes=90)

# --------------------------------------------------------------------------- #
# Recognizable brands — the pre-committed list for the brand stratum.
#
# Picked by eye from live Brand facet counts: household names a shopper would
# recognize, versus the marketplace no-names (UIMIU, conulog, Lcutml, ...) that
# dominate the index. This is a judgment call and the weakest link in the brand
# test, so it is written down ONCE, before any data is collected, rather than
# adjusted later to make results look better.
#
# Known limitation, accepted going in: brand is coarse. Logitech spans a $120
# mouse and a $14 dongle, which sell at very different fractions of retail. This
# tests whether brand carries ANY signal, not whether it is the right variable.
# --------------------------------------------------------------------------- #
RECOGNIZABLE_BRANDS: list[str] = [
    "Amazon Basics", "Samsung", "Apple", "Sony", "LG", "Panasonic", "HP",
    "Logitech", "Beats", "Roku", "GE", "BLACK+DECKER", "Frigidaire", "Midea",
    "BRITA", "Kohler", "Leviton", "Dorman", "Permatex", "Conair", "Intex",
    "Zinus", "Bedsure", "Graco", "Evenflo", "Momcozy", "Dr. Brown's",
    "Dream On Me", "HUGGIES", "Pampers", "Starbucks", "NISSIN", "Little Debbie",
    "Entenmann's", "NYX PROFESSIONAL MAKEUP", "MAYBELLINE", "COVERGIRL",
    "wet n wild", "e.l.f.", "CeraVe", "Olaplex", "The Ordinary", "stila",
    "Native", "Just for Men", "Nexcare", "Hanes", "Vanity Fair", "Avery",
    "Funko", "Bemis", "Adams", "JETech", "ESR",
]

# Star strata for the targeted pull. ★1/★2/★3 are collapsed because ★1 has only
# ~28 lots in an entire window — no sample size can rescue it — and the earlier
# (now deleted) data found ★1/★2/★3 statistically inseparable anyway.
STAR_STRATA: list[tuple[str, list[str]]] = [
    ("star_4", ["Star Rating >= 4", "Star Rating < 5"]),
    ("star_low", ["Star Rating < 4"]),
]

# Algolia fields to pull. This is an ALLOWLIST — anything omitted comes back
# missing, silently. Kept in sync with query_mapping.py's list.
ATTRIBUTES = [
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
]
