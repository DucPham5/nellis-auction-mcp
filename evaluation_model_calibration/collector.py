"""The nightly calibration collector.

Run as a state machine, not a long-running process. Each invocation asks "what
round is due right now?", does that one round, and exits. Cron calls it every 15
minutes. That design exists for one mundane reason: the previous round of this
work lost its most valuable data because an afternoon run "simply never
happened". A sleeping laptop, a closed terminal, or a crash now costs at most one
partial round instead of the night.

Usage:
    python -m evaluation_model_calibration.collector init [--date YYYY-MM-DD]
    python -m evaluation_model_calibration.collector tick
    python -m evaluation_model_calibration.collector status
"""

from __future__ import annotations

import argparse
import contextlib
import fcntl
import json
import random
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from nellis import detail_source
from nellis.errors import NellisBlockedError, NellisError

from . import run_config as cfg
from . import sampling

# How late a round may still run. Cron jitter and short sleeps are fine — every
# row records its true observed_at and hours_to_close, so a late round is still
# honest data, just at a different point on the curve. Past this, the observation
# is too close to (or past) close to be what the round was for, and it's recorded
# as missed rather than silently mislabelled.
GRACE = timedelta(hours=2)


@contextlib.contextmanager
def single_instance():
    """Refuse to run two rounds at once.

    The finals round fetches ~2,400 lots at ~1 req/s (~40 min) but launchd ticks
    every 15 minutes, so without this a second process would start mid-round and
    both would append to the same file — double-fetching lots and doubling our
    request rate against Nellis. flock is released automatically if the process
    dies, so a crash can't leave a stale lock behind.
    """
    cfg.DATA_DIR.mkdir(parents=True, exist_ok=True)
    lock_path = cfg.DATA_DIR / ".collector.lock"
    fh = lock_path.open("w")
    try:
        try:
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print("Another round is already running; exiting.")
            yield False
            return
        yield True
    finally:
        fh.close()


# --------------------------------------------------------------------------- #
# Run directory + state
# --------------------------------------------------------------------------- #
def run_dir(close_date: datetime) -> Path:
    return cfg.DATA_DIR / f"run_{close_date:%Y-%m-%d}"


def _state_path(d: Path) -> Path:
    return d / "state.json"


def load_state(d: Path) -> dict:
    return json.loads(_state_path(d).read_text())


def save_state(d: Path, state: dict) -> None:
    tmp = _state_path(d).with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, indent=2))
    tmp.replace(_state_path(d))          # atomic: never leave a half-written state


def append_jsonl(path: Path, rows: list[dict]) -> None:
    with path.open("a") as fh:
        for row in rows:
            fh.write(json.dumps(row) + "\n")


def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def _round_schedule(close_date: datetime) -> list[tuple[str, datetime]]:
    """Every round and when it is due, in order."""
    rounds: list[tuple[str, datetime]] = []
    for name, (hh, mm) in cfg.SNAPSHOT_ROUNDS:
        rounds.append((name, close_date.replace(
            hour=hh, minute=mm, second=0, microsecond=0, tzinfo=cfg.LOCAL_TZ)))
    finals_day = close_date + timedelta(days=1)
    rounds.append(("finals", finals_day.replace(
        hour=cfg.FINALS_TIME[0], minute=cfg.FINALS_TIME[1],
        second=0, microsecond=0, tzinfo=cfg.LOCAL_TZ)))
    return rounds


def build_snapshot_subset(rows: list[dict], rng: random.Random) -> list[str]:
    """Pick which lots get re-read at every snapshot round.

    Weighted toward the brand comparison (see run_config.SNAPSHOT_STRATA): the
    branded arm and a population arm of equal size, so Q11 can be split by
    bid count without collapsing into ~40-lot cells.
    """
    by_source: dict[str, list[str]] = {}
    for row in rows:
        by_source.setdefault(row["sample_source"], []).append(row["id"])

    subset: list[str] = []
    for stratum in cfg.SNAPSHOT_STRATA:
        ids = by_source.get(stratum, [])
        take = min(cfg.SNAPSHOT_PER_STRATUM, len(ids))
        subset.extend(rng.sample(ids, take))
    return subset


def cmd_add_location_compare() -> None:
    """Add the Houston/Dallas comparison stratum to the current run, in place.

    Purely additive: appends to lots.jsonl, extends the snapshot subset so
    future scheduled rounds pick the new lots up automatically, and does
    nothing to anything already collected. `finals` already reads every id in
    lots.jsonl when it fires, so no schedule change is needed for final prices —
    only `snap_t0` needs a manual top-up since it already ran before these lots
    were added (done separately via `snap-now`).
    """
    runs = sorted(cfg.DATA_DIR.glob("run_*")) if cfg.DATA_DIR.exists() else []
    if not runs:
        print("No runs found.")
        return
    d = runs[-1]
    state = load_state(d)
    close_date = datetime.strptime(state["close_date"], "%Y-%m-%d")

    existing = read_jsonl(d / "lots.jsonl")
    existing_ids = {r["id"] for r in existing}

    new_rows = sampling.draw_location_comparison(close_date, existing_ids)
    if not new_rows:
        print("No new Houston/Dallas-area lots found.")
        return

    append_jsonl(d / "lots.jsonl", new_rows)

    new_ids = [r["id"] for r in new_rows]
    state["snapshot_subset"] = sorted(set(state.get("snapshot_subset", [])) | set(new_ids))
    counts = state.get("sample_counts", {})
    counts["location_houston_dallas"] = len(new_rows)
    state["sample_counts"] = counts
    save_state(d, state)

    print(f"Added {len(new_rows)} Houston/Dallas-area lots to {d.name}")
    print(f"Snapshot subset: {len(state['snapshot_subset'])} total (includes the new lots)")
    print("Run `snap-now --label snap_t0` to backfill their first snapshot.")


def cmd_rebuild_subset() -> None:
    """Recompute the snapshot subset for the existing run, in place.

    Existing snapshot rows are kept — rounds are idempotent by (id, round), so
    lots already observed are skipped and only the newly-added ones get fetched.
    """
    runs = sorted(cfg.DATA_DIR.glob("run_*")) if cfg.DATA_DIR.exists() else []
    if not runs:
        print("No runs found.")
        return
    d = runs[-1]
    state = load_state(d)
    rows = read_jsonl(d / "lots.jsonl")
    old = set(state.get("snapshot_subset", []))

    rng = random.Random(state.get("seed"))
    new = build_snapshot_subset(rows, rng)
    # Keep anything already observed so no lot loses its earliest data point.
    merged = sorted(set(new) | old)

    state["snapshot_subset"] = merged
    save_state(d, state)

    counts: dict[str, int] = {}
    by_id = {r["id"]: r for r in rows}
    for pid in merged:
        src = by_id.get(pid, {}).get("sample_source", "?")
        counts[src] = counts.get(src, 0) + 1
    print(f"Snapshot subset: {len(old)} -> {len(merged)} lots")
    for k, v in sorted(counts.items()):
        print(f"   {k:16s} {v}")


# --------------------------------------------------------------------------- #
# init — Pass 1
# --------------------------------------------------------------------------- #
def cmd_init(close_date: datetime, seed: int | None) -> None:
    d = run_dir(close_date)
    if _state_path(d).exists():
        print(f"Run already exists: {d}\nUse `tick` to continue it.")
        return
    d.mkdir(parents=True, exist_ok=True)

    print(f"Pass 1 — sampling lots closing {close_date:%Y-%m-%d} "
          f"{cfg.CLOSE_WINDOW_START[0]:02d}:00-{cfg.CLOSE_WINDOW_END[0]:02d}:59 local ...")
    rows = sampling.draw_sample(close_date, seed=seed)

    if not rows:
        print("No lots sampled — check the close window/date. Nothing written.")
        return

    append_jsonl(d / "lots.jsonl", rows)

    rng = random.Random(seed)
    subset = build_snapshot_subset(rows, rng)

    counts: dict[str, int] = {}
    for row in rows:
        counts[row["sample_source"]] = counts.get(row["sample_source"], 0) + 1

    state = {
        "close_date": f"{close_date:%Y-%m-%d}",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "seed": seed,
        "sample_counts": counts,
        "snapshot_subset": subset,
        "rounds": {"pass1": {"status": "done",
                             "finished_at": datetime.now(timezone.utc).isoformat(),
                             "count": len(rows)}},
    }
    save_state(d, state)

    print(f"\nSampled {len(rows)} lots -> {d/'lots.jsonl'}")
    for k, v in sorted(counts.items()):
        print(f"   {k:16s} {v}")
    print(f"Snapshot subset: {len(subset)} lots")
    print(f"\nNext: add the cron entry (see `status`), or run `tick` manually.")


# --------------------------------------------------------------------------- #
# tick — run whichever round is due
# --------------------------------------------------------------------------- #
def _fetch_round(d: Path, state: dict, name: str, ids: list[str],
                 out_file: str, close_lookup: dict[str, str]) -> str:
    """Fetch pricing for ``ids``, appending rows. Returns the round's end status.

    Idempotent: ids already recorded for this round are skipped, so a killed run
    resumes rather than duplicating.
    """
    out_path = d / out_file
    already = {r["id"] for r in read_jsonl(out_path) if r.get("round") == name}
    todo = [i for i in ids if i not in already]

    if name != "finals":
        # A round's own subset spans the whole 6-hour close window, but a
        # fixed-clock round late in that window (e.g. 21:00) fires AFTER a large
        # share of that same window has already closed — 71% of one night's
        # snap_21 subset, measured directly, entirely independent of any delay.
        # Fetching those is pure waste: `finals` re-fetches every lot fresh a
        # few hours later regardless, so an already-closed lot here is a request
        # spent on data that gets superseded shortly anyway.
        now = datetime.now(timezone.utc)
        before = len(todo)
        todo = [
            i for i in todo
            if not close_lookup.get(i) or datetime.fromisoformat(close_lookup[i]) > now
        ]
        skipped = before - len(todo)
        if skipped:
            print(f"[{name}] skipping {skipped} lots already past close "
                  f"(finals will read them fresh)")

    if not todo:
        return "done"

    print(f"[{name}] {len(todo)} lots to fetch ({len(already)} already done)")
    buf: list[dict] = []
    errors: list[dict] = []
    done = 0

    for pid in todo:
        now = datetime.now(timezone.utc)
        try:
            pricing = detail_source.fetch_pricing(pid)
        except NellisBlockedError as exc:
            # The one error we must not push through — stop immediately.
            append_jsonl(out_path, buf)
            append_jsonl(d / "errors.jsonl", errors)
            print(f"[{name}] BLOCKED after {done}: {exc}", file=sys.stderr)
            return "blocked"
        except (NellisError, Exception) as exc:  # noqa: BLE001 - never lose a round to one lot
            errors.append({"id": pid, "round": name,
                           "at": now.isoformat(), "error": f"{type(exc).__name__}: {exc}"})
            continue

        closes_at = close_lookup.get(pid)
        hours = None
        if closes_at:
            hours = (datetime.fromisoformat(closes_at) - now).total_seconds() / 3600.0

        buf.append({
            "id": pid,
            "round": name,
            "observed_at": now.isoformat(),
            "hours_to_close": hours,
            "current_bid": str(pricing.current_bid) if pricing.current_bid is not None else None,
            "est_retail": str(pricing.est_retail) if pricing.est_retail is not None else None,
            "bid_count": pricing.bid_count,
            "market_status": pricing.market_status,
        })
        done += 1

        # Flush periodically so a crash costs seconds of work, not the round.
        # Errors flush on the same beat: they're the record of WHICH lots are
        # missing and why, so losing them to a kill would leave silent gaps.
        if len(buf) >= 25:
            append_jsonl(out_path, buf)
            buf = []
            if errors:
                append_jsonl(d / "errors.jsonl", errors)
                errors = []
            print(f"[{name}] {done}/{len(todo)}", flush=True)

    append_jsonl(out_path, buf)
    if errors:
        append_jsonl(d / "errors.jsonl", errors)
    print(f"[{name}] complete: {done} ok, {len(errors)} failed")
    return "done"


def cmd_tick() -> None:
    """Run one due round, across ALL runs — oldest first.

    Iterating every run (not just the newest) is essential once nights overlap:
    night 1's finals fall at 01:00 Wednesday, but night 2 is started Tuesday
    evening. If tick only looked at the latest run, night 1's finals would never
    fire and that entire night would be sampled and snapshotted with no outcome
    data — the one thing that makes it worth collecting. Oldest-first also means
    a pending finals round is never starved by a newer run's snapshots.
    """
    if _maybe_auto_init():
        return

    runs = sorted(cfg.DATA_DIR.glob("run_*")) if cfg.DATA_DIR.exists() else []
    if not runs:
        print("No runs found. Start one with `init`.")
        return
    for d in runs:
        if _tick_run(d):
            return
    print("Nothing due.")


def _maybe_auto_init() -> bool:
    """Start tomorrow's run if we're at the pinned sampling time. True if started.

    Sampling immediately followed by a t0 snapshot mirrors night 1's procedure
    exactly. Keeping the procedure identical between nights is the whole point of
    pinning the time — see run_config.AUTO_INIT_AT.
    """
    if not cfg.AUTO_INIT:
        return False

    now_local = datetime.now(cfg.LOCAL_TZ)
    target = now_local.replace(hour=cfg.AUTO_INIT_AT[0], minute=cfg.AUTO_INIT_AT[1],
                               second=0, microsecond=0)
    if not (target <= now_local <= target + cfg.AUTO_INIT_GRACE):
        return False

    close_date = (now_local + timedelta(days=1)).replace(tzinfo=None)
    if _state_path(run_dir(close_date)).exists():
        return False                       # already started tonight

    seed = int(f"{close_date:%Y%m%d}")     # reproducible, and distinct per night
    print(f"Auto-init: starting run for close date {close_date:%Y-%m-%d}")
    cmd_init(close_date, seed)
    # t0 immediately after sampling — Algolia carries no bid data, so without
    # this the night has no long-lead observation at all.
    cmd_snap_now("snap_t0")
    return True


def _tick_run(d: Path) -> bool:
    """Run one due round for a single run. Returns True if it did any work."""
    state = load_state(d)
    close_date = datetime.strptime(state["close_date"], "%Y-%m-%d")

    lots = read_jsonl(d / "lots.jsonl")
    close_lookup = {r["id"]: r["closes_at"] for r in lots if r.get("closes_at")}
    subset = state.get("snapshot_subset", [])
    all_ids = [r["id"] for r in lots]

    now = datetime.now(timezone.utc)
    for name, due in _round_schedule(close_date):
        rec = state["rounds"].get(name, {})
        if rec.get("status") == "done":
            continue
        due_utc = due.astimezone(timezone.utc)
        if now < due_utc:
            break                                  # nothing due yet
        if now > due_utc + GRACE and name != "finals":
            # Too late for this observation to mean what the round intended.
            state["rounds"][name] = {"status": "missed", "due": due_utc.isoformat()}
            save_state(d, state)
            print(f"[{name}] missed (was due {due_utc.isoformat()})")
            continue

        ids = all_ids if name == "finals" else subset
        out_file = "finals.jsonl" if name == "finals" else "snapshots.jsonl"
        state["rounds"][name] = {"status": "running", "started_at": now.isoformat()}
        save_state(d, state)

        status = _fetch_round(d, state, name, ids, out_file, close_lookup)
        state["rounds"][name] = {
            "status": status if status == "done" else "incomplete",
            "finished_at": datetime.now(timezone.utc).isoformat(),
        }
        save_state(d, state)
        return True                                # one round per tick

    return False


def cmd_snap_now(label: str) -> None:
    """Take a snapshot round immediately, under an explicit label.

    Pass 1 records only Algolia metadata, and Algolia carries no bid data at all —
    so without this there is NO bid observation at the long-lead mark (~20-26h
    out), which is precisely where the earlier work found its strongest predictor.
    Run this right after `init`.
    """
    runs = sorted(cfg.DATA_DIR.glob("run_*")) if cfg.DATA_DIR.exists() else []
    if not runs:
        print("No runs found. Start one with `init`.")
        return
    d = runs[-1]
    state = load_state(d)
    lots = read_jsonl(d / "lots.jsonl")
    close_lookup = {r["id"]: r["closes_at"] for r in lots if r.get("closes_at")}
    subset = state.get("snapshot_subset", [])

    state["rounds"][label] = {"status": "running",
                              "started_at": datetime.now(timezone.utc).isoformat()}
    save_state(d, state)
    status = _fetch_round(d, state, label, subset, "snapshots.jsonl", close_lookup)
    state["rounds"][label] = {"status": status if status == "done" else "incomplete",
                              "finished_at": datetime.now(timezone.utc).isoformat()}
    save_state(d, state)


def cmd_status() -> None:
    runs = sorted(cfg.DATA_DIR.glob("run_*")) if cfg.DATA_DIR.exists() else []
    if not runs:
        print("No runs yet.")
        return
    for d in runs:
        state = load_state(d)
        print(f"\n{d.name}   sampled: {state.get('sample_counts')}")
        close_date = datetime.strptime(state["close_date"], "%Y-%m-%d")
        scheduled = _round_schedule(close_date)
        # Ad-hoc rounds (snap-now) aren't on the schedule but are real data —
        # show them or `status` silently omits work that actually happened.
        listed = {n for n, _ in scheduled} | {"pass1"}
        adhoc = [(n, None) for n in state["rounds"] if n not in listed]
        for name, due in [("pass1", None)] + adhoc + scheduled:
            rec = state["rounds"].get(name, {})
            when = f"  due {due:%H:%M}" if due else "  (ad-hoc)"
            print(f"   {name:12s} {rec.get('status', 'pending'):12s}{when}")
        for f in ("lots.jsonl", "snapshots.jsonl", "finals.jsonl", "errors.jsonl"):
            p = d / f
            if p.exists():
                print(f"   {f:18s} {len(read_jsonl(p))} rows")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p_init = sub.add_parser("init", help="draw the sample (Pass 1) and create the run")
    p_init.add_argument("--date", help="close date, YYYY-MM-DD (default: tomorrow)")
    p_init.add_argument("--seed", type=int, default=None)
    sub.add_parser("tick", help="run whichever round is due now")
    p_snap = sub.add_parser("snap-now", help="take a snapshot round immediately")
    p_snap.add_argument("--label", default="snap_t0")
    sub.add_parser("rebuild-subset", help="recompute the snapshot subset in place")
    sub.add_parser("add-location-compare", help="add the Houston/Dallas comparison stratum")
    sub.add_parser("status", help="show run progress")
    args = ap.parse_args()

    if args.cmd == "init":
        if args.date:
            close_date = datetime.strptime(args.date, "%Y-%m-%d")
        else:
            close_date = datetime.now(cfg.LOCAL_TZ).replace(tzinfo=None) + timedelta(days=1)
        cmd_init(close_date, args.seed)
    elif args.cmd == "tick":
        with single_instance() as ok:
            if ok:
                cmd_tick()
    elif args.cmd == "snap-now":
        with single_instance() as ok:
            if ok:
                cmd_snap_now(args.label)
    elif args.cmd == "rebuild-subset":
        cmd_rebuild_subset()
    elif args.cmd == "add-location-compare":
        cmd_add_location_compare()
    else:
        cmd_status()


if __name__ == "__main__":
    main()
