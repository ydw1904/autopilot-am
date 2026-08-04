#!/usr/bin/env python3
"""
Daily routine -- the mobile freebies, once a day, in a random order.

Tasks (every one is bounded to free actions; none of them spends a balance):
  currencies  free shop offers (5x/day each) + the travel-card wheel
  slots       the slot machine's free daily games, ONLY while a spin-milestone
              event is running -- the event is read live off specialEvent, so
              no calendar of event windows has to be maintained here
  donate      the alliance treasury donation, maxed at the daily cap. This one
              rides the BROWSER (Chrome CDP), not the mobile API, so it needs a
              logged-in tab; it is idempotent, so a repeat run donates 0

The game's day rolls over at 00:00 UTC, so this is meant to run shortly after.
--jitter sleeps a random 0..N minutes before starting, so the wall-clock time
differs from day to day rather than hitting the API at the same second.

The mobile token expires daily. When a task comes back with an auth error the
routine runs tools/mobile-capture/refresh_mobile_session.sh once and retries
that task -- which needs BlueStacks running, as the runbook describes.

Usage:
  python3 daily_routine.py --dry-run
  python3 daily_routine.py
  python3 daily_routine.py --jitter 35 --log ~/.airlines_manager/daily.log
  python3 daily_routine.py --only slots
"""

import argparse
import json
import os
import random
import subprocess
import sys
import time
from datetime import datetime, timezone

CODE_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_DIR = os.path.dirname(CODE_DIR)
REFRESH_SCRIPT = os.path.join(REPO_DIR, "tools", "mobile-capture",
                              "refresh_mobile_session.sh")

# Dollars held back from the alliance donation. The cap is $200M/day against a
# balance in the billions, so 0 is the sane default; --reserve overrides it.
DONATION_RESERVE = 0

sys.path.insert(0, CODE_DIR)


def _tool(name):
    """The plain function behind an MCP tool, so the routine can call it."""
    import mcp_server
    fn = getattr(mcp_server, name)
    return getattr(fn, "fn", fn)


def _is_auth_error(result: dict) -> bool:
    if result.get("auth_expired"):
        return True
    if result.get("ok") is False:
        return "expired" in str(result.get("error", "")).lower()
    return False


# ── tasks ───────────────────────────────────────────────────────────────────
# Each takes (dry_run) and returns the tool's result dict. Keep them free-only.

def task_currencies(dry_run: bool) -> dict:
    """Free shop currency claims + the travel-card wheel (spin and respin)."""
    return _tool("mobile_daily_bonuses")(dry_run=dry_run)


def task_slots(dry_run: bool) -> dict:
    """The slot machine's free games -- skipped unless an event is running.

    The milestone gift (a livery) is what makes the ~20 minutes of spinning
    worth it, so outside an event window this is a deliberate no-op.
    """
    import mcp_server
    probe = _tool("mobile_daily_slot")(dry_run=True)
    if not probe.get("ok"):
        return probe
    event = probe.get("event")
    if not event:
        return {"ok": True, "skipped": "no spin-milestone event running",
                "free_games": probe.get("free_games")}
    if event.get("spins_to_go") == 0:
        return {"ok": True, "skipped": "event milestone already reached",
                "event": event}
    return _tool("mobile_daily_slot")(dry_run=dry_run)


def task_donate(dry_run: bool) -> dict:
    """Max out the daily alliance donation.

    The odd one out: this is the browser/CDP surface, not the mobile API, so it
    needs Chrome up on port 9222 with a logged-in tab. A dead web session fails
    this task alone — the mobile refresh can't fix it, so it says so plainly.
    """
    import alliance_donator
    return alliance_donator.run(dry_run=dry_run, reserve=DONATION_RESERVE)


TASKS = {
    "currencies": task_currencies,
    "slots": task_slots,
    "donate": task_donate,
}


# ── runner ──────────────────────────────────────────────────────────────────

def log(msg: str, fh=None) -> None:
    line = f"[{datetime.now(timezone.utc):%Y-%m-%d %H:%M:%S}Z] {msg}"
    print(line, flush=True)
    if fh:
        fh.write(line + "\n")
        fh.flush()


def refresh_session(fh=None) -> bool:
    """Re-import the mobile token from a fresh capture. Needs BlueStacks up."""
    if not os.access(REFRESH_SCRIPT, os.X_OK):
        log(f"  refresh script not executable: {REFRESH_SCRIPT}", fh)
        return False
    log("  token expired -- refreshing the mobile session", fh)
    try:
        p = subprocess.run([REFRESH_SCRIPT], capture_output=True, text=True,
                           timeout=300)
    except subprocess.TimeoutExpired:
        log("  refresh timed out", fh)
        return False
    ok = "Session refreshed" in p.stdout
    log("  refresh " + ("ok" if ok else "FAILED (is BlueStacks running?)"), fh)
    return ok


def run_task(name: str, dry_run: bool, fh=None) -> dict:
    """Run one task, retrying once behind a session refresh on an auth error."""
    log(f"-> {name}", fh)
    try:
        result = TASKS[name](dry_run)
    except Exception as e:                      # never let one task kill the run
        result = {"ok": False, "error": f"{type(e).__name__}: {e}"}
    if _is_auth_error(result) and not dry_run:
        if refresh_session(fh):
            try:
                result = TASKS[name](dry_run)
            except Exception as e:
                result = {"ok": False, "error": f"{type(e).__name__}: {e}"}
    log(f"   {json.dumps(result)[:400]}", fh)
    return result


def main():
    global DONATION_RESERVE
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--dry-run", action="store_true",
                   help="report what each task would do, change nothing")
    p.add_argument("--jitter", type=int, default=0,
                   help="sleep a random 0..N minutes before starting")
    p.add_argument("--log", help="append the run log to this file")
    p.add_argument("--only", action="append", choices=sorted(TASKS),
                   help="run just these tasks (repeatable)")
    p.add_argument("--json", action="store_true",
                   help="print the result document to stdout as JSON")
    p.add_argument("--reserve", type=int, default=DONATION_RESERVE,
                   help="dollars to keep on hand when donating (default 0)")
    args = p.parse_args()
    DONATION_RESERVE = args.reserve

    fh = open(os.path.expanduser(args.log), "a") if args.log else None
    try:
        names = args.only or list(TASKS)
        random.shuffle(names)                   # no fixed fingerprint per day
        log(f"daily routine start (order: {', '.join(names)}"
            f"{', dry-run' if args.dry_run else ''})", fh)

        if args.jitter:
            wait = random.uniform(0, args.jitter * 60)
            log(f"jitter: sleeping {wait / 60:.1f} min", fh)
            time.sleep(wait)

        results = {}
        for i, name in enumerate(names):
            if i:
                time.sleep(random.uniform(20, 90))   # human-ish gap between tasks
            results[name] = run_task(name, args.dry_run, fh)

        failed = [n for n, r in results.items() if r.get("ok") is False]
        log(f"done -- {len(results) - len(failed)}/{len(results)} ok"
            + (f", failed: {', '.join(failed)}" if failed else ""), fh)
        if args.json:
            json.dump({"results": results, "failed": failed}, sys.stdout, indent=1)
            print()
        return 1 if failed else 0
    finally:
        if fh:
            fh.close()


if __name__ == "__main__":
    sys.exit(main())
