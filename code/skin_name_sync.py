#!/usr/bin/env python3
"""Backfill livery NAMES — and where each livery is sold — into `mobile_skins`.

The fleet syncs identify a livery by picture and id only, so hundreds of owned
aircraft carry a `skin_id` with no name against it. Three independent sources
fill that in, each covering what the others cannot:

  --web       the browser game's reconfigure page. Every aircraft's page ships
              a hidden `#aircraftSkinJson` listing the liveries THAT aircraft
              can wear, names included, and the one it currently wears is
              always among them. It is the only source that names an awarded
              livery once its challenge or event is over and the reward ladder
              below has moved on. One page fetch per unnamed livery, not per
              aircraft.
  --dutyfree  the mobile duty free (`shop/skin/getSkins/{page}`), 3k liveries
              with names, prices, creators and an owned flag, split into the
              shop's own `playrion` and `market` buckets.
  --shm       the second-hand market's live listings, whose `skin.type` is the
              game's own Playrion / player-made / manufacturer verdict.
  --challenge the running challenge's reward ladder (`challenge/`), the only
              endpoint that names the challenge liveries while they are still
              being awarded — the shop never sells them.
  --shop      the shop feed (`shop2023/offers`): packs and battle passes list
              the liveries they contain, with the livery's own type stated.

Together they set `mobile_skins.source`, which is what separates an official
Playrion livery from one an airline designed and sells on the livery market.

Usage:
  code/skin_name_sync.py                  # all three
  code/skin_name_sync.py --dutyfree       # one pass only (flags combine)
  code/skin_name_sync.py --web --limit 20
  code/skin_name_sync.py --shm --shm-shallow
  code/skin_name_sync.py --dry-run

The web pass needs Chrome on --remote-debugging-port=9222 with an AM tab; the
other two need only the mobile session (~/.airlines_manager/session.json).
"""

from __future__ import annotations

import argparse
import html
import json
import logging
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import db
from mobile_api import (AMClient, AMSession, AUCTION_PAGE_LIMIT,
                        SKIN_SOURCE_MANUFACTURER, SKIN_SOURCE_MARKET,
                        SKIN_TYPE_ARTIST, SKIN_TYPE_MANUFACTURER,
                        SKIN_TYPE_PLAYRION)
from mobile_store import MobileStore

# httpx logs full request URLs at INFO and the mobile API passes access_token
# in the query string, so an unsuppressed run prints the live token.
logging.disable(logging.INFO)

# The reconfigure page's livery carousel, as a JSON blob in a hidden input:
#   [{"id":…,"name":…,"price":…,"unlocked":…,"superBigPicture":…}, …]
SKIN_JSON_RE = re.compile(r'value="([^"]*)"\s+id="aircraftSkinJson"')
# Belt and braces: the page also prints the CURRENT livery's name in the clear.
CURRENT_NAME_RE = re.compile(r'id="deliverySkinName">([^<]*)<')


def picture_path(url: str | None) -> str | None:
    """Normalise a carousel picture URL to a stored `picture_path`.

    The carousel hands back an absolute CDN URL at superBig; everything else in
    the DB stores a host-less path at big, matched on its basename.
    """
    if not url:
        return None
    path = re.sub(r"^https?://[^/]+", "", url).split("?")[0]
    return path.replace("/skins/superBig/", "/skins/big/") or None


def web_source(name: str | None, path: str | None) -> str | None:
    """What the reconfigure page alone can prove about a livery's origin.

    A `painterPublic` picture is a player-designed livery by construction, and
    the game names a model's own paint "<model> - (Manufacturer livery)".
    Anything else could be Playrion, a challenge reward or an event drop, and
    the page does not say which — left unset for the API passes to fill in.
    """
    if path and "/painterPublic/" in path:
        return SKIN_SOURCE_MARKET
    if name and name.strip().endswith("(Manufacturer livery)"):
        return SKIN_SOURCE_MANUFACTURER
    return None


def unnamed_owned_skins() -> list[tuple[int, int, str]]:
    """(skin_id, an aircraft wearing it, that aircraft's model) for every owned
    livery with no name yet. One aircraft per livery is all the page needs."""
    return [tuple(r) for r in db.get_db().execute("""
        SELECT f.skin_id, MIN(f.aircraft_id), MIN(f.model)
          FROM fleet f
          JOIN mobile_skins s ON s.skin_id = f.skin_id
         WHERE f.skin_id IS NOT NULL AND s.name IS NULL
         GROUP BY f.skin_id
         ORDER BY COUNT(*) DESC
    """).fetchall()]


def sync_web(store: MobileStore, limit: int, dry_run: bool) -> None:
    from cdp import CDP, get_am_tab
    from aircraft_reconfigurator import _fetch_with_retry

    todo = unnamed_owned_skins()
    if limit:
        todo = todo[:limit]
    print(f"== web reconfigure pages ==\n  {len(todo)} owned liveries with no name")
    if not todo:
        return

    tab = get_am_tab()
    if not tab:
        print("  no AM tab found — start Chrome with --remote-debugging-port=9222")
        return
    cdp = CDP(tab["webSocketDebuggerUrl"])
    cdp.connect()
    named = failed = extra = 0
    try:
        for i, (skin_id, aircraft_id, model) in enumerate(todo, 1):
            page = _fetch_with_retry(cdp, f"/aircraft/show/{aircraft_id}/reconfigure")
            m = SKIN_JSON_RE.search(page or "")
            if not m:
                failed += 1
                print(f"  [{i}/{len(todo)}] {skin_id:<9} {model:<14} "
                      f"no carousel on aircraft {aircraft_id}")
                continue
            entries = json.loads(html.unescape(m.group(1)))
            by_id = {e.get("id"): e for e in entries}
            hit = by_id.get(skin_id)
            if hit is None:
                # The carousel is per aircraft, so this only happens if the
                # fleet's idea of the livery is stale; the page's own label is
                # then the truth about what the plane is wearing.
                current = CURRENT_NAME_RE.search(page or "")
                failed += 1
                print(f"  [{i}/{len(todo)}] {skin_id:<9} {model:<14} not in its own "
                      f"carousel (wears {current.group(1).strip() if current else '?'})")
                continue
            named += 1
            print(f"  [{i}/{len(todo)}] {skin_id:<9} {model:<14} {hit.get('name')}")
            if dry_run:
                continue
            # Record the whole carousel, not just the livery we came for: the
            # rest are names for liveries this airline may not own yet.
            for e in entries:
                path = picture_path(e.get("superBigPicture"))
                store.upsert_skin(e.get("id"), name=e.get("name"),
                                  picture_path=path,
                                  source=web_source(e.get("name"), path))
                extra += 1
            if i % 25 == 0:
                store.commit()
        if not dry_run:
            store.commit()
    finally:
        cdp.close()
    print(f"  done: {named} named, {failed} unresolved, "
          f"{extra} carousel entries recorded")


def sync_dutyfree(client: AMClient, store: MobileStore, dry_run: bool) -> None:
    print("== duty free ==")
    for source in ("playrion", "market"):
        seen = names = owned = 0
        for sk in client.shop_skins(source=source):
            seen += 1
            names += bool(sk.get("name"))
            owned += bool(sk.get("purchased"))
        print(f"  {source:<9} {seen:>5} liveries, {names:>5} named, {owned:>4} owned")
    if not dry_run:
        store.commit()


def _skin_delta(store: MobileStore, before: tuple) -> str:
    """How many liveries a pass added, and how many it named for the first time."""
    after = _skin_state(store)
    return (f"{after[0] - before[0]} liveries new to the DB, "
            f"{after[1] - before[1]} named for the first time")


def _skin_state(store: MobileStore) -> tuple:
    row = store.conn.execute(
        "SELECT COUNT(*), SUM(name IS NOT NULL) FROM mobile_skins").fetchone()
    return (row[0] or 0, row[1] or 0)


def split_by_paint(store: MobileStore, skin_ids: set) -> tuple[list, list]:
    """Split livery ids into (special, factory paint).

    Both reward feeds hand out plain aircraft as often as painted ones — half
    the shop's offers are a model in its default colours, and a challenge
    ladder is mostly stock planes with a few exclusives on top. The game says
    which is which itself: an aircraft reward's `skin.type` is 0 for a
    manufacturer paint, and those liveries are named "<model> - (Manufacturer
    livery)". `mobile_skins.source` carries that verdict, so the split is a
    lookup, not a guess.
    """
    if not skin_ids:
        return [], []
    marks = ",".join("?" * len(skin_ids))
    rows = store.conn.execute(
        f"""SELECT skin_id, name, COALESCE(source, '') = 'manufacturer' AS is_manu
              FROM mobile_skins WHERE skin_id IN ({marks})""",
        tuple(skin_ids)).fetchall()
    special = [r["name"] or str(r["skin_id"]) for r in rows if not r["is_manu"]]
    factory = [r["name"] or str(r["skin_id"]) for r in rows if r["is_manu"]]
    return sorted(special), sorted(factory)


def sync_challenge(client: AMClient, store: MobileStore, dry_run: bool) -> None:
    """Read the running challenge's reward ladder.

    Challenge liveries are awarded, never sold, so the duty free has never
    heard of them — the ladder is where they are named, exactly as the booster
    drop table names the booster ones.
    """
    print("== challenge ==")
    before = _skin_state(store)
    challenges = client.challenges()
    if not challenges:
        print("  no challenge running")
        return
    for ch in challenges:
        objectives = ch.get("objectives") or []
        rewards = [r for o in objectives
                   for key in ("rewards", "battlePassRewards")
                   for r in (o.get(key) or [])]
        skins = {(r.get("skin") or {}).get("id") for r in rewards} - {None}
        window = (f"{_date_of(ch.get('startDate'))} → "
                  f"{_date_of(ch.get('endDate'))}")
        prog = ch.get("airlineProgress") or {}
        print(f"  [{ch.get('id')}] {ch.get('title')}  ({ch.get('challengeType')})"
              f"  {window}")
        special, factory = split_by_paint(store, skins)
        print(f"        {len(objectives)} objectives, {len(rewards)} reward slots, "
              f"{len(skins)} distinct liveries ({len(special)} special, "
              f"{len(factory)} factory paint); rank {prog.get('rank')} "
              f"at {prog.get('progress')}")
        for a in (ch.get("aircraft") or []):
            print(f"        x{a.get('multiplier')}  {a.get('id'):<9} {a.get('name')}")
    print(f"  {_skin_delta(store, before)}")
    if not dry_run:
        store.commit()


def sync_shop(client: AMClient, store: MobileStore, dry_run: bool) -> None:
    """Read the shop feed — packs and battle passes carry liveries too."""
    print("== shop offers ==")
    before = _skin_state(store)
    offers = client.shop_offers()
    with_skins = [o for o in offers
                  if any((it or {}).get("skin") for it in (o.get("content") or []))]
    skins = {(it.get("skin") or {}).get("id")
             for o in offers for it in (o.get("content") or [])
             if isinstance(it, dict) and it.get("skin")} - {None}
    special, factory = split_by_paint(store, skins)
    print(f"  {len(offers)} offers, {len(with_skins)} of them carry an aircraft, "
          f"{len(skins)} distinct liveries "
          f"({len(special)} special, {len(factory)} factory paint)")
    for o in with_skins:
        ids = {(it.get("skin") or {}).get("id")
               for it in (o.get("content") or [])
               if isinstance(it, dict) and it.get("skin")} - {None}
        sp, fa = split_by_paint(store, ids)
        # An offer whose every aircraft is a stock paint sells the plane, not a
        # livery — worth calling out, because it looks identical in the feed.
        note = "plane only" if not sp else f"{len(sp)} livery"
        cost = o.get("purchaseCost")
        print(f"  [{o.get('id'):>6}] {(o.get('title') or '')[:38]:<38} "
              f"{o.get('template') or '':<10} {len(ids):>2} aircraft  "
              f"{note:<9} {cost} {o.get('purchaseCurrency') or ''}")
    print(f"  {_skin_delta(store, before)}")
    if not dry_run:
        store.commit()


def _date_of(v) -> str:
    if isinstance(v, dict):
        return (v.get("date") or "")[:10]
    return str(v or "")[:10]


def sync_shm(client: AMClient, store: MobileStore, deep: bool,
             dry_run: bool) -> None:
    """Sweep live listings for the game's own livery classification.

    `auction_list` answers with at most AUCTION_PAGE_LIMIT listings and takes no
    page parameter, so a read that comes back at the limit is a truncated view
    of that filter. The way past it is a second, narrower filter: sweeping the
    models one at a time keeps every read comfortably under the cap.
    """
    print("== second-hand market ==")
    types = ((SKIN_TYPE_MANUFACTURER, "manufacturer"),
             (SKIN_TYPE_PLAYRION, "playrion"),
             (SKIN_TYPE_ARTIST, "market"))
    truncated = []
    for skin_type, label in types:
        listings = client.auctions(skin_type=skin_type)
        skins = {(a.get("aircraft") or {}).get("skin", {}).get("id")
                 for a in listings} - {None}
        full = len(listings) >= AUCTION_PAGE_LIMIT
        print(f"  type {skin_type} ({label:<12}) {len(listings):>3} listings, "
              f"{len(skins):>3} liveries{'  [TRUNCATED]' if full else ''}")
        if full:
            truncated.append((skin_type, label))

    if truncated and deep:
        models = [m["id"] for m in
                  client._request("GET", "bfa/model").get("modelList", [])]
        print(f"  sweeping {len(models)} models for "
              f"{', '.join(l for _, l in truncated)}")
        for skin_type, label in truncated:
            found = capped = 0
            for n, model_id in enumerate(models, 1):
                listings = client.auctions(skin_type=skin_type, model_id=model_id)
                found += len({(a.get("aircraft") or {}).get("skin", {}).get("id")
                              for a in listings} - {None})
                capped += len(listings) >= AUCTION_PAGE_LIMIT
                if n % 40 == 0:
                    print(f"    {label}: {n}/{len(models)} models, "
                          f"{found} liveries seen")
            note = f", {capped} models still at the cap" if capped else ""
            print(f"    {label}: {found} liveries across {len(models)} models{note}")
    elif truncated:
        print("  (rerun without --shm-shallow to sweep the truncated types "
              "model by model)")
    if not dry_run:
        store.commit()


def classify_by_artwork(store: MobileStore, dry_run: bool) -> int:
    """Class the liveries no shop and no listing could speak for.

    Event, challenge and long-retired seasonal liveries are awarded rather than
    sold, so the duty free has never heard of them and they surface on the SHM
    only if someone happens to be selling one. What is left is still decidable
    from the artwork: a player-designed livery is served out of `painterPublic`
    and an official one out of the game's own `Aircrafts/skins` directory,
    which only Playrion can write to. The split held for all 3,167 liveries the
    API passes had already classed (every market one under `painterPublic`,
    every Playrion and manufacturer one not), so it is applied here as the
    fallback — never over a class an endpoint stated outright.
    """
    undecided = "source IS NULL AND picture_path IS NOT NULL"
    n = store.conn.execute(
        f"SELECT COUNT(*) FROM mobile_skins WHERE {undecided}").fetchone()[0]
    print(f"\n== artwork fallback ==\n  {n} liveries no endpoint classed")
    if n and not dry_run:
        store.conn.execute(f"""
            UPDATE mobile_skins
               SET source = CASE WHEN picture_path LIKE '%/painterPublic/%'
                                 THEN 'market' ELSE 'playrion' END
             WHERE {undecided}""")
        store.commit()
    return n


def report(store: MobileStore) -> None:
    print("\n== mobile_skins ==")
    row = store.conn.execute("""
        SELECT COUNT(*),
               SUM(name IS NOT NULL),
               SUM(source IS NOT NULL)
          FROM mobile_skins""").fetchone()
    print(f"  {row[0]} liveries, {row[1]} named, {row[2]} with a known source")
    for source, n, flown in store.conn.execute("""
        SELECT COALESCE(s.source, '(unknown)'), COUNT(DISTINCT s.skin_id),
               COUNT(DISTINCT f.skin_id)
          FROM mobile_skins s
          LEFT JOIN fleet f ON f.skin_id = s.skin_id
         GROUP BY 1 ORDER BY 2 DESC"""):
        print(f"    {source:<13} {n:>5} liveries, {flown:>4} of them on the fleet")
    left = store.conn.execute("""
        SELECT COUNT(DISTINCT f.skin_id), COUNT(*)
          FROM fleet f JOIN mobile_skins s ON s.skin_id = f.skin_id
         WHERE s.name IS NULL""").fetchone()
    print(f"  owned liveries still unnamed: {left[0]} "
          f"({left[1]} aircraft)")


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--web", action="store_true",
                    help="name owned liveries off the reconfigure page (needs CDP)")
    ap.add_argument("--dutyfree", action="store_true",
                    help="sync the duty free livery catalogue")
    ap.add_argument("--shm", action="store_true",
                    help="sync livery classes off live market listings")
    ap.add_argument("--challenge", action="store_true",
                    help="sync the running challenge's reward ladder")
    ap.add_argument("--shop", action="store_true",
                    help="sync the shop feed's packs and battle passes")
    ap.add_argument("--limit", type=int, default=0,
                    help="with --web, cap how many liveries to chase (0 = all)")
    ap.add_argument("--shm-shallow", action="store_true",
                    help="with --shm, skip the per-model sweep of truncated reads")
    ap.add_argument("--dry-run", action="store_true",
                    help="read and report, write nothing")
    args = ap.parse_args()

    # No pass named means every pass.
    passes = (args.web, args.dutyfree, args.shm, args.challenge, args.shop)
    if not any(passes):
        args.web = args.dutyfree = args.shm = True
        args.challenge = args.shop = True

    store = MobileStore()
    client = None
    if args.dutyfree or args.shm or args.challenge or args.shop:
        session = AMSession.load()
        try:
            session.renew()
        except Exception as e:                        # noqa: BLE001
            print(f"session renew failed: {e}", file=sys.stderr)
            return 1
        # In dry-run the client must not write through its store hooks.
        client = AMClient(session, min_delay=0.2,
                          store=None if args.dry_run else store)
    try:
        if args.web:
            sync_web(store, args.limit, args.dry_run)
        if args.dutyfree:
            sync_dutyfree(client, store, args.dry_run)
        if args.shm:
            sync_shm(client, store, not args.shm_shallow, args.dry_run)
        if args.challenge:
            sync_challenge(client, store, args.dry_run)
        if args.shop:
            sync_shop(client, store, args.dry_run)
    finally:
        if client:
            client.close()
    classify_by_artwork(store, args.dry_run)
    report(store)
    return 0


if __name__ == "__main__":
    sys.exit(main())
