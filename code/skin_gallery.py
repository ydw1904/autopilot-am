#!/usr/bin/env python3
"""Browse the livery artwork cached in mobile_skin_images as an HTML contact sheet.

Two output modes:
  --out DIR      write the PNGs next to an index.html that links them (fast,
                 no size limit, best for the whole set)
  --embed FILE   one self-contained .html with the images inlined as data URIs
                 (portable/shareable, ~1.35x the byte size of the PNGs)

Filters compose, so you can look at just the part you care about:
  code/skin_gallery.py --out /tmp/liveries
  code/skin_gallery.py --booster "South America" --embed /tmp/sa.html
  code/skin_gallery.py --missing --rarity 4 --out /tmp/wanted
  code/skin_gallery.py --name sharky --embed /tmp/sharky.html
"""

from __future__ import annotations

import argparse
import base64
import html
import os
import sqlite3
import sys

import db as _db

QUERY = """
SELECT o.skin_id, o.name, o.rarity, o.owned_aircraft, o.best_card_rate,
       o.boosters, i.png, i.byte_len
  FROM mobile_skin_overview o
  JOIN mobile_skin_images i ON i.skin_id = o.skin_id AND i.size = ?
 WHERE 1=1
"""


def rows(conn, args):
    sql, params = QUERY, [args.size]
    if args.booster:
        sql += " AND o.boosters LIKE ?"
        params.append(f"%{args.booster}%")
    if args.rarity is not None:
        sql += " AND o.rarity = ?"
        params.append(args.rarity)
    if args.owned:
        sql += " AND o.owned_aircraft > 0"
    if args.missing:
        sql += " AND o.owned_aircraft = 0"
    if args.name:
        sql += " AND o.name LIKE ?"
        params.append(f"%{args.name}%")
    # rarest and least-owned first: that is the collector's reading order
    sql += (" ORDER BY COALESCE(o.rarity,-1) DESC, o.owned_aircraft ASC,"
            " o.name IS NULL, o.name")
    if args.limit:
        sql += f" LIMIT {int(args.limit)}"
    return conn.execute(sql, params).fetchall()


CSS = """
:root{--bg:#f6f7f9;--card:#fff;--ink:#1c1e21;--dim:#6b7280;--line:#e3e6ea;
      --accent:#b45309;--own:#047857}
:root:not([data-theme=light]){}
@media (prefers-color-scheme:dark){:root:not([data-theme=light]){
  --bg:#14161a;--card:#1d2025;--ink:#e8eaed;--dim:#9aa3af;--line:#2c3037;
  --accent:#fbbf24;--own:#34d399}}
:root[data-theme=dark]{--bg:#14161a;--card:#1d2025;--ink:#e8eaed;--dim:#9aa3af;
  --line:#2c3037;--accent:#fbbf24;--own:#34d399}
*{box-sizing:border-box}
body{margin:0;padding:24px;background:var(--bg);color:var(--ink);
     font:14px/1.45 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif}
h1{font-size:19px;margin:0 0 4px}
.sub{color:var(--dim);margin-bottom:18px}
#q{width:100%;max-width:420px;padding:8px 11px;border:1px solid var(--line);
   border-radius:8px;background:var(--card);color:var(--ink);margin-bottom:18px}
.grid{display:grid;gap:14px;grid-template-columns:repeat(auto-fill,minmax(300px,1fr))}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;
      padding:12px;display:flex;flex-direction:column;gap:8px}
.card img{width:100%;height:auto;image-rendering:auto}
.nm{font-weight:600;word-break:break-word}
.meta{color:var(--dim);font-size:12px;display:flex;flex-wrap:wrap;gap:10px}
.r4{color:var(--accent);font-weight:700}
.own{color:var(--own);font-weight:600}
.none{opacity:.55}
"""

JS = """
const q=document.getElementById('q');
q.addEventListener('input',()=>{const v=q.value.toLowerCase();
 document.querySelectorAll('.card').forEach(c=>{
  c.style.display=c.dataset.s.includes(v)?'':'none';});});
"""


def card_html(r, src):
    skin_id, name, rarity, owned, rate, boosters, _, blen = r
    nm = html.escape(name or f"(unnamed skin {skin_id})")
    bits = [f"id {skin_id}"]
    if rarity is not None:
        bits.append(f'<span class="{"r4" if rarity == 4 else ""}">r{rarity}</span>')
    bits.append(f'<span class="own">{owned} in fleet</span>' if owned
                else '<span class="none">not owned</span>')
    if rate:
        bits.append(f"{rate:.3f}%/card")
    if boosters:
        bits.append(html.escape(boosters))
    search = f"{nm} {skin_id} {boosters or ''}".lower()
    return (f'<div class="card" data-s="{html.escape(search, quote=True)}">'
            f'<img src="{src}" alt="{nm}" loading="lazy">'
            f'<div class="nm">{nm}</div>'
            f'<div class="meta">{"".join(f"<span>{b}</span>" for b in bits)}</div>'
            f"</div>")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", help="directory: PNGs + index.html")
    ap.add_argument("--embed", help="single self-contained .html")
    ap.add_argument("--size", default="big", choices=("medium", "big", "superBig"))
    ap.add_argument("--booster", help="only liveries dropped by this booster")
    ap.add_argument("--rarity", type=int)
    ap.add_argument("--owned", action="store_true", help="only ones you fly")
    ap.add_argument("--missing", action="store_true", help="only ones you don't")
    ap.add_argument("--name", help="substring match on the livery name")
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()
    if not args.out and not args.embed:
        ap.error("need --out DIR or --embed FILE")

    conn = sqlite3.connect(_db.DB)
    data = rows(conn, args)
    if not data:
        print("no liveries matched", file=sys.stderr)
        return 1

    cards, total = [], 0
    if args.out:
        img_dir = os.path.join(args.out, "png")
        os.makedirs(img_dir, exist_ok=True)
        for r in data:
            fn = f"{r[0]}.png"
            with open(os.path.join(img_dir, fn), "wb") as fh:
                fh.write(r[6])
            total += r[7]
            cards.append(card_html(r, f"png/{fn}"))
        target = os.path.join(args.out, "index.html")
    else:
        for r in data:
            b64 = base64.b64encode(r[6]).decode()
            total += r[7]
            cards.append(card_html(r, f"data:image/png;base64,{b64}"))
        target = args.embed

    owned = sum(1 for r in data if r[3])
    sub = (f"{len(data)} liveries at {args.size} &middot; {owned} in your fleet, "
           f"{len(data) - owned} not &middot; {total / 1048576:.1f}MB of artwork")
    doc = (f"<!doctype html><html><head><meta charset=utf-8>"
           f"<meta name=viewport content='width=device-width,initial-scale=1'>"
           f"<title>Livery gallery</title><style>{CSS}</style></head><body>"
           f"<h1>Livery gallery</h1><div class=sub>{sub}</div>"
           f"<input id=q placeholder='Filter by name, id or booster…'>"
           f"<div class=grid>{''.join(cards)}</div>"
           f"<script>{JS}</script></body></html>")
    os.makedirs(os.path.dirname(os.path.abspath(target)), exist_ok=True)
    with open(target, "w", encoding="utf-8") as fh:
        fh.write(doc)
    print(f"{len(data)} liveries -> {target} "
          f"({os.path.getsize(target) / 1048576:.1f}MB page)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
