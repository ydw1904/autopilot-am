#!/usr/bin/env python3
"""Sync booster drop tables (and livery artwork) into the shared SQLite DB.

Fills three tables:
  mobile_boosters       one row per active booster (window, prices, pity gauge)
  mobile_booster_cards  one row per card in a booster's published drop table
  mobile_skin_images    the livery PNG itself, as bytes

Why this matters beyond the boosters: `GET booster/droprate` is the only
endpoint that returns a real livery NAME for a skin the player does not own,
so syncing it backfills `mobile_skins.name` for hundreds of rows that the
fleet/auction reads only ever saw as a bare id.

The drop table endpoint was found by capturing the app on the booster contents
screen (tools/mobile-capture/); it cannot be reached by guessing.

Usage:
  code/booster_sync.py                      # drop tables only
  code/booster_sync.py --images big         # also pull artwork it is missing
  code/booster_sync.py --images big --all-skins   # artwork for every known skin
  code/booster_sync.py --dry-run
"""

from __future__ import annotations

import argparse
import logging
import sys

import mobile_api
from mobile_api import AMSession, AMClient, SKIN_IMAGE_SIZES
from mobile_store import MobileStore

# httpx logs full request URLs at INFO and the mobile API passes access_token
# in the query string, so an unsuppressed run prints the live token.
logging.disable(logging.INFO)


def human(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:,.1f}{unit}" if unit != "B" else f"{n:,}B"
        n /= 1024.0


def sync_droprates(client: AMClient, store: MobileStore, dry_run: bool) -> None:
    print("== boosters ==")
    boosters = client.boosters()
    for b in boosters:
        pity = b.get("pity") or {}
        window = ""
        if b.get("endDate"):
            window = f"  ends {(b['endDate'] or {}).get('date', '')[:10]}"
        print(f"  [{b['id']:>4}] {b['name']:<16} event={bool(b.get('isEvent'))!s:<5}"
              f" pity={pity.get('gaugeCount')}/{pity.get('gaugeMax')}{window}")

    print("\n== drop tables ==")
    for b in boosters:
        try:
            payload = client.booster_droprate(b["id"])
        except Exception as e:                       # noqa: BLE001
            print(f"  [{b['id']}] {b['name']}: FAILED {type(e).__name__}: {e}")
            continue
        groups = payload.get("dropRates") or []
        cards = sum(len(g.get("cards") or []) for g in groups)
        skins = sum(1 for g in groups for c in (g.get("cards") or []) if c.get("skin"))
        print(f"  [{b['id']:>4}] {b['name']:<16} {len(groups)} groups, "
              f"{cards:>3} cards, {skins:>3} carry a skin")
        for g in groups:
            n = len(g.get("cards") or [])
            per = (g.get("dropRate") or 0) / n if n else 0
            # the API hands back raw floats (72.89999999999999); round for display
            print(f"          r{g.get('rarity')} {round(g.get('dropRate') or 0, 4):>7}%  "
                  f"{n:>3} cards  ({per:.4f}% each)")
    if not dry_run:
        store.commit()


def sync_images(client: AMClient, store: MobileStore, size: str,
                all_skins: bool, limit: int, dry_run: bool) -> None:
    print(f"\n== artwork ({size}) ==")
    # A picture_path is the prerequisite; the boot manifest is the widest source.
    client.skin_catalog()
    todo = store.skins_missing_image(size)
    if not all_skins:
        booster_skins = {r[0] for r in store.conn.execute(
            "SELECT DISTINCT skin_id FROM mobile_booster_cards "
            "WHERE skin_id IS NOT NULL")}
        todo = [t for t in todo if t[0] in booster_skins]
    if limit:
        todo = todo[:limit]
    print(f"  {len(todo)} skins missing a {size} image")
    if dry_run:
        for skin_id, path in todo[:10]:
            print(f"    would fetch {skin_id:<9} {path.rsplit('/', 1)[-1]}")
        return

    ok = failed = total_bytes = 0
    cdn = mobile_api.skin_image_client()
    for i, (skin_id, path) in enumerate(todo, 1):
        try:
            data, url = mobile_api.fetch_skin_png(path, size=size, client=cdn)
        except Exception as e:                        # noqa: BLE001
            failed += 1
            print(f"    [{i}/{len(todo)}] {skin_id} FAILED {type(e).__name__}: "
                  f"{str(e)[:70]}")
            continue
        store.store_skin_image(skin_id, size, data, url)
        ok += 1
        total_bytes += len(data)
        if i % 25 == 0 or i == len(todo):
            store.commit()
            print(f"    [{i}/{len(todo)}] {ok} stored, {failed} failed, "
                  f"{human(total_bytes)}")
    cdn.close()
    store.commit()
    print(f"  done: {ok} stored, {failed} failed, {human(total_bytes)}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--images", choices=SKIN_IMAGE_SIZES,
                    help="also download livery PNGs at this size")
    ap.add_argument("--all-skins", action="store_true",
                    help="with --images, fetch every known skin, not just booster ones")
    ap.add_argument("--limit", type=int, default=0,
                    help="cap how many images to fetch this run (0 = no cap)")
    ap.add_argument("--skip-droprates", action="store_true")
    ap.add_argument("--dry-run", action="store_true",
                    help="read and report, write nothing")
    args = ap.parse_args()

    session = AMSession.load()
    try:
        session.renew()
    except Exception as e:                            # noqa: BLE001
        print(f"session renew failed: {e}", file=sys.stderr)
        return 1

    store = MobileStore()
    # In dry-run the client must not write through its store hooks.
    client = AMClient(session, store=None if args.dry_run else store)
    try:
        if not args.skip_droprates:
            sync_droprates(client, store, args.dry_run)
        if args.images:
            sync_images(client, store, args.images, args.all_skins,
                        args.limit, args.dry_run)
    finally:
        client.close()

    print("\n== table counts ==")
    for table, n in store.counts().items():
        print(f"  {table:<22} {n}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
