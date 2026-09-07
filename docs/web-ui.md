# Browser UI (`web/src/`)

## Dropdowns (browser UI) — `web/src/components/MenuSelect.tsx`

Every dropdown in `web/src/` — filters, sorts, pickers — uses `<MenuSelect>`. Do
not add a native `<select>`: the OS popup it opens is tiny, unstyleable, and
looks foreign against the dark UI. There are no `<select>` elements left in the
repo, so a new one is always a mistake.

```tsx
<MenuSelect label="Hub" value={hub} onChange={setHub} options={hubOptions} icon={Building2} />
<MenuSelect label="Sort by" value={sort} onChange={setSort} groups={SORT_GROUPS} />
```

- `options` for a flat list, `groups` for one split by headings; each option is
  `{ value, label, hint?, icon? }` where `hint` is the short qualifier printed
  next to the label ("A to Z", "12 aircraft").
- `label` is the caption above the current value; `icon` is the trigger icon
  used when the selected option carries none. `align="right"` makes the panel
  line up with the trigger's right edge, for controls near a container's edge.
- Styling lives in `web/src/index.css` under `.menu-select*`. The panel is
  absolutely positioned, so a container that wraps one must not set
  `overflow: hidden` (see the comment on `.fleet-controls`).
- **Sort menus:** name ascending is always the first entry and the default,
  name descending second, then the remaining fields as a "most first" /
  "least first" pair. Spell the direction out in `hint` — never a `+` / `-`
  suffix or a bare arrow.
- Only offer a sort the data can actually deliver. `FLEET_SORTS` in `db.py` is
  the full set of fleet orderings; anything not in that table silently falls
  back to name order.

## Aircraft tags (browser UI) — `web/src/components/TagPicker.tsx`

Custom aircraft tags are free text, so the selection bar uses `<TagPicker>` (a
combobox) rather than `<MenuSelect>` (a closed set). It lists the tags already
in use — `stats.tags`, with live counts — above the presets in
`PRESET_AIRCRAFT_TAGS`, so a tag is retyped only the first time it is coined.
Picking a row applies it immediately; typing something new offers a "Create …"
row so Enter never applies a near-match nobody chose. Tags carried by every
selected aircraft are ticked (adding one again is a no-op: `db.py` inserts with
`INSERT OR IGNORE` under a NOCASE key). A preset disappears from "Suggested"
once it exists in "Your tags", and an unused preset is written nowhere until it
is applied. Its panel opens **upward** — the selection bar is pinned to the
bottom of the viewport.

A tag edit must **not** call `onDataChanged()`. That bumps `refreshToken`, which
re-runs the fleet query, replaces the grid with the loading line, clears the
selection, and drops the reader back to the top of the page. `applyTagResult()`
in `FleetWorkspace.tsx` patches the affected rows from the PATCH response and
re-reads `stats` on its own for the counts. For the same reason a refetch dims
the rows already on screen (`.is-refetching`) instead of unmounting them.

## Read-only workspaces — Network / Circuits / Pricing / Ops

Four tabs that give the remaining CLI tools a home in the browser app. All
four are **read-only on purpose**: the writes they front (route buying,
scheduling, repricing, claiming) have real preconditions — 24h cooldowns,
demand constraints, CDP-only form submits — that the CLI scripts already
encode, and duplicating them in the UI is how the two drift apart.

- **Network** (`/api/network` → `db.get_network_snapshot`, coordinates from
  one cached mobile world-catalog read) — the owned routes only, as a table and
  on a map; planned routes join in behind one "Include planned" switch. The
  snapshot still carries the circuits and each route's `circuits` list, but the
  tab does not show them: circuits live in their own tab.
  - The map is `RouteMap.tsx`: one `<canvas>` drawn with `d3-geo`, coastlines
    from `world-atlas/land-110m.json` (56 KB, the one asset the bundle carries).
    The flat map is a Natural Earth projection under `d3-zoom` (wheel to zoom,
    drag to pan, the canvas transform does the scaling so the projected paths
    are built once). The globe is an orthographic projection: pointer drag
    rotates it, wheel zooms, and it spins on its own after 2.5s idle. None of
    it is React state; every interaction redraws the canvas directly. Only
    hubs get a dot and a label — 1,000 destination dots is noise.
  - Clicking a row opens that route's detail page, which carries everything the
    game's own ROUTE DETAILS page shows. **Three reads feed it, each rendering
    as it lands** — the page must stay useful when any one of them is down:
    - `/api/route/{hub}/{dest}` — the cached `routes` row plus a live
      `line/{id}` mobile read: purchase price and date, resale value,
      incidents, and the audit's date / reliability / price / demand.
    - `/api/pricing/{hub}` — live per-class price, carried volume and
      remaining demand (the last drives pricing, and is the one figure the
      game's own page does *not* print).
    - `/api/route/{hub}/{dest}/details` — `route_details.fetch_showline`, the
      `/network/showline/{id}` scrape. **CDP-only**, so it is a separate
      endpoint: the rest of the page must not wait ~1.2s on it, and a
      Chrome-less setup gets a populated `error` instead of a failed request.
      This is the *only* source for airport taxes, accepted categories,
      flights per week, the Today/Yesterday statistics, the D-5..today history,
      the D+5 forecast, the 7-day financial summary, and the list of aircraft
      actually scheduled on the line.

    Daily capacity per class (`2 x seats x waves`, summed over the circuits
    carrying the route) sits beside the game's real "Offer" so the plan can be
    read against what actually flew. Without the scrape the aircraft section
    falls back to `FleetBrowser` scoped to the circuit's `<name>-` prefix.
- **Circuits** (same `/api/network` snapshot) — a board, not a table. A hub
  tile row comes first (revenue, circuits, aircraft, and a bar against the
  richest hub); clicking one *is* the hub filter, which is why there is no hub
  dropdown. Below it the circuits are cards — weekly revenue with a bar sized
  against the best circuit on screen, `done/total` meters for routes, waves and
  fleet, the destination chips (solid = owned, dashed = still to buy), and the
  gaps as badges. A **planned** circuit has every one of those gaps by
  definition, so its badges are muted and it never counts toward the "N need
  attention" on a hub tile. The rest of the fleet toolbar stays (status tabs
  with counts, aircraft / attention filters, grouped sort, "N active filters /
  Clear all"). Opening a circuit swaps the
  tab for its detail page: per-route price, planned seat capacity, audited
  demand, remaining demand, and revenue per class — *actual* is current price
  × pax carried, *max* is current price × audited demand, both per day from
  `/api/pricing/{hub}` — then the assigned aircraft as tiles or a list.
  Aircraft are matched to a circuit by the `<HUB>-C<NNN>-<MMM>` name
  `aircraft_numberer.py` writes; the `fleet` table has no circuit column, so
  that prefix *is* the join. Nothing on the tab writes yet.
- **Pricing** (`/api/pricing/{hub}` read, `/api/pricing/{hub}/apply` write) —
  one `masstool.fetch_hub` call per hub (mobile, CDP fallback), showing current
  price against the audit's already corrected recommendation, unsold demand,
  and the 24h lock. Sorted by the eco gap because eco carries the volume.
  The write is `mobile_pricer.price_hub` — see below.
- **Ops** (`/api/ops`) — the delivery waiting list (with a countdown against the
  *server* clock, not the browser's), what daily rewards are still claimable,
  and how stale each cached table is. The two mobile sections carry their own
  `error` field rather than failing the whole response: a dead mobile session
  must not hide the freshness table, which is exactly what you check when the
  session dies.

Tables in these tabs use the shared `.grid-table` CSS. `.shm-table` predates it
and keeps its own fixed column widths.

## The pricing apply flow

`POST /api/pricing/{hub}/apply` is a thin wrapper over `mobile_pricer.price_hub`
— same modes (`ideal` / `percent` / `fill`), same document, same per-route
`status` (`dry-run` / `skipped` / `cooldown` / `ok` / `fail`). Two guards exist
only on the endpoint, because a stray POST is far easier to make than a stray
shell command:

- **`pct` is clamped to 25-200%.** A fat-fingered multiplier would not merely
  misprice the hub, it would burn every route's 24h cooldown getting there.
- **A live write (`dry_run=False`) must name its routes.** "Reprice the whole
  hub" is therefore reachable only as a preview. The CLI keeps the unrestricted
  path; the UI does not need it.

The tab enforces preview-then-apply on top of that: the Apply button sends
exactly the IATAs the dry run reported as `dry-run`, never the scope, so a
write can only touch rows the operator actually saw. Changing hub, mode, `pct`,
or scope discards the open plan rather than leaving an "apply 98 changes"
button pointed at numbers nobody read. Applying takes two clicks (Apply →
Confirm), the same weight the aircraft editor gives its destructive writes.

## Aircraft editor (browser UI) — `web/src/components/AircraftEditor.tsx`

The single-aircraft workbench, opened from the Fleet tab: clicking any aircraft
there (or one in Circuits) navigates to `#fleet?preset=ac:<id>`, and
`FleetWorkspace` swaps its browser for the editor while that preset is set.
Every write the mobile API can make against the plane lives here — rename, hub,
livery, seats, market listing, scrap. It is deliberately **one aircraft at a
time**; bulk renames and tags stay in the fleet browser, and bulk market
listings stay in `shm_sell_batch`, which already respects the 10-listing cap.

There is no second fleet picker: "All aircraft" goes back to the browser with
its filters intact, and the bar's jump search (eight cached-table matches, or
the last eight planes opened when nothing is typed, kept in `localStorage`)
switches planes without leaving the editor. A former `#hangar` tab held the
same editor; `readLocation` in `App.tsx` still maps that hash to `#fleet` so old
links land.

Backend: `/api/hangar/{id}` (+ `/liveries`, `/schedule`, and one POST per
action) in `api_server.py`. All of them go through `_hangar_call`, which builds
the mobile client and maps a refused write to an HTTP error, so a failure
reaches the operator as text instead of a silent no-op. There is no CDP
fallback here by design — the whole point is that these endpoints answer
`status: 0` on refusal.

Three rules the endpoints enforce, not the UI (the UI only warns first):

- **A rename echoes the current seat map back**, which is what keeps it free.
  The seats are re-read from the profile per call; never take them from a
  cached row.
- **Repainting over a non-manufacturer livery needs `confirm_overwrite`.** An
  awarded challenge or event livery cannot be re-applied once painted over.
- **Scrapping needs the aircraft's name typed back** in `confirm_name`, and it
  drops the cached `fleet` row on success. The underlying write is the
  unverified one (see the mobile API surface above).

Clearing a schedule is the one editor action still on CDP — the mobile planning
write payload has never been captured (ticket 013, gap 1) — so it returns 503
with that explanation when Chrome is not linked, rather than pretending the
mobile path exists.

Every successful write re-reads the profile and patches the cached `fleet` row
(`_sync_fleet_row`), so a rename or hub move shows up in Fleet Operations
without a full sync.

