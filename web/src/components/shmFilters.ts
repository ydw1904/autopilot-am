import { ShmDecision, ShmModelCheck, ShmSighting, ShmWatch } from "../types";
import { splitLiveryName } from "../liveryName";
import type { MenuGroup } from "./MenuSelect";

/** How the tab names, colours and icons each way a watch got on the list. The
 *  class names are the livery-tag palette from the Liveries tab, so a paid pack
 *  is the same blue in both places. */
export const SOURCE_STYLES: Record<string, { label: string; cls: string; hint?: string }> = {
  "shop-pack:auto": { label: "Paid pack", cls: "is-shop-pack" },
  "shop-ticket:auto": { label: "Ticket aircraft", cls: "is-shop-tc", hint: "costs travel cards" },
  "shop-amc:auto": { label: "AM coin aircraft", cls: "is-shop-amc", hint: "costs AM coins" },
  "shop-gold:auto": { label: "AM Gold Crew", cls: "is-shop-gift", hint: "monthly subscription reward" },
  "challenge:auto": { label: "Challenge", cls: "is-challenge" },
  booster: { label: "Booster", cls: "is-booster" },
  manual: { label: "Manual", cls: "is-manual" },
};

/** A watch's source column is free text; these are the buckets the filter offers. */
export function sourceBucket(source: string | null): string {
  if (source && source in SOURCE_STYLES && source !== "manual") return source;
  if (source?.startsWith("booster:")) return "booster";
  return "manual";
}

export function sourceStyle(source: string | null) {
  return SOURCE_STYLES[sourceBucket(source)];
}

/** Kinds whose `origin` names a specific event worth filtering on. A shop
 *  origin is an offer title with a price, so it stays at the kind level. */
const EVENT_BUCKETS = new Set(["booster", "challenge:auto"]);

/** Source filter value: a bare bucket, or `bucket|origin` for one event. */
export function matchesSource(watch: ShmWatch, filter: string): boolean {
  if (filter === "all") return true;
  const [bucket, origin] = filter.split("|");
  return sourceBucket(watch.source) === bucket && (origin === undefined || watch.origin === origin);
}

/** The Source menu, one group per kind present in the list: "All boosters"
 *  first, then each booster or challenge event with its count. */
export function sourceGroups(watches: ShmWatch[]): MenuGroup[] {
  const counts = new Map<string, number>();
  for (const watch of watches) {
    const bucket = sourceBucket(watch.source);
    counts.set(bucket, (counts.get(bucket) ?? 0) + 1);
    if (EVENT_BUCKETS.has(bucket) && watch.origin) {
      const key = `${bucket}|${watch.origin}`;
      counts.set(key, (counts.get(key) ?? 0) + 1);
    }
  }
  const hint = (n: number | undefined) => `${n ?? 0}`;
  const groups: MenuGroup[] =
    [{ options: [{ value: "all", label: "All sources", hint: hint(watches.length) }] }];
  for (const [bucket, style] of Object.entries(SOURCE_STYLES)) {
    if (!counts.has(bucket)) continue;
    const events = [...counts.keys()].filter((key) => key.startsWith(`${bucket}|`)).sort();
    const options = [{ value: bucket, label: events.length ? `All ${style.label.toLowerCase()}s` : style.label, hint: hint(counts.get(bucket)) },
      ...events.map((key) => ({ value: key, label: key.split("|")[1], hint: hint(counts.get(key)) }))];
    groups.push(events.length ? { label: style.label, options } : { options });
  }
  return groups;
}

/** The model half of a watch label ("737-700 - Virgo Blue" -> "737-700"). The
 *  watcher stores the game's own skin name, which always leads with the model,
 *  so the numeric model id never has to be shown as the primary identifier. */
export function watchModel(watch: ShmWatch): string {
  return splitLiveryName(watch.label).model;
}

export function watchLivery(watch: ShmWatch): string {
  return splitLiveryName(watch.label).livery;
}

/** model_id -> model name, learned from the watch labels. The sightings and
 *  coverage feeds carry only the id, and "E195-E2" beats "model 142". */
export function modelNames(watches: ShmWatch[]): Map<number, string> {
  const names = new Map<number, string>();
  for (const watch of watches) {
    const model = watchModel(watch);
    if (watch.model_id !== null && model && !names.has(watch.model_id)) {
      names.set(watch.model_id, model);
    }
  }
  return names;
}

/** What the watcher will actually do with a row tonight.
 *
 *  `select_watches` in shm_watcher.py reads `active=1 AND bought < want`, so a
 *  row outside that is dormant however its arm flag reads — the tab has to say
 *  so rather than offering a toggle that changes nothing. */
export type WatchState = "armed" | "observing" | "fulfilled" | "retired";

export function watchState(watch: ShmWatch): WatchState {
  if (watch.active && watch.bought < watch.want) return watch.armed ? "armed" : "observing";
  return watch.bought >= watch.want ? "fulfilled" : "retired";
}

export function isLiveWatch(watch: ShmWatch): boolean {
  const state = watchState(watch);
  return state === "armed" || state === "observing";
}

export const STATE_LABELS: Record<WatchState, { label: string; cls: string; hint: string }> = {
  armed: { label: "Armed", cls: "is-armed", hint: "Buys this livery on sight, up to the price cap" },
  observing: { label: "Observe", cls: "is-observing", hint: "Tracked only — no money is spent" },
  fulfilled: { label: "Acquired", cls: "is-fulfilled", hint: "Already in the hangar, so the watcher stopped pursuing it" },
  retired: { label: "Retired", cls: "is-retired", hint: "No longer offered, so the watcher dropped it" },
};

/** Armed first, dormant last: the column sorts the way the queue is worked. */
const STATE_RANK: Record<WatchState, number> = { armed: 0, observing: 1, fulfilled: 2, retired: 3 };

/** 1 ascending, -1 descending. */
export type SortDir = 1 | -1;

export interface SortSpec<T> {
  /** Column heading, also the label the sort control shows. */
  label: string;
  /** Direction the first click on this column applies. */
  dir: SortDir;
  /** How each direction reads in a sort menu: [ascending, descending]. Required
   *  because AGENTS.md spells a direction out ("Newest first") rather than
   *  hanging a bare arrow or a +/- suffix off the label. */
  hints: [string, string];
  value: (item: T) => number | string | null;
}

// Nulls always sink, whichever way the column points: a livery never seen on the
// market has no price to rank by, so it belongs at the bottom of both directions
// rather than flipping to the top when you reverse the sort.
function cmp(x: number | string | null, y: number | string | null, dir: SortDir): number {
  if (x === null) return y === null ? 0 : 1;
  if (y === null) return -1;
  return x < y ? -dir : x > y ? dir : 0;
}

/** Comparator for `specs[key]`; an unknown key keeps the server's own ordering. */
export function sortBy<T>(specs: Record<string, SortSpec<T>>, key: string, dir: SortDir) {
  const spec = specs[key];
  return (a: T, b: T) => (spec ? cmp(spec.value(a), spec.value(b), dir) : 0);
}

/** A sort menu carries the whole choice in one value, "key:dir", so the pair is
 *  a single MenuSelect option rather than a dropdown plus a direction button. */
export function sortByValue<T>(specs: Record<string, SortSpec<T>>, value: string) {
  const [key, dir] = value.split(":");
  return sortBy(specs, key, dir === "-1" ? -1 : 1);
}

/** Same column again flips direction; a third click drops back to `resetKey`. */
export function nextSort<T>(
  specs: Record<string, SortSpec<T>>,
  current: { key: string; dir: SortDir },
  key: string,
  resetKey: string,
): { key: string; dir: SortDir } {
  if (current.key !== key) return { key, dir: specs[key].dir };
  const flipped = (current.dir === 1 ? -1 : 1) as SortDir;
  return flipped === specs[key].dir ? { key: resetKey, dir: 1 } : { key, dir: flipped };
}

export const WATCH_SORTS: Record<string, SortSpec<ShmWatch>> = {
  label: { label: "Livery", dir: 1, hints: ["A to Z", "Z to A"], value: (w) => watchLivery(w).toLowerCase() },
  source: { label: "Source", dir: 1, hints: ["A to Z", "Z to A"], value: (w) => sourceStyle(w.source).label },
  state: { label: "Standing order", dir: 1, hints: ["Armed first", "Dormant first"], value: (w) => STATE_RANK[watchState(w)] },
  cap: { label: "Price cap", dir: -1, hints: ["Lowest first", "Highest first"], value: (w) => w.max_price },
  cheapest: { label: "Cheapest seen", dir: 1, hints: ["Cheapest first", "Priciest first"], value: (w) => w.cheapest_seen },
  last_price: { label: "Last price", dir: 1, hints: ["Cheapest first", "Priciest first"], value: (w) => w.last_price_seen },
  sightings: { label: "Listings", dir: -1, hints: ["Fewest first", "Most first"], value: (w) => w.sightings },
  last_seen: { label: "Last seen", dir: -1, hints: ["Oldest first", "Newest first"], value: (w) => w.last_seen },
};

/** Column order of the watched-liveries table. Filtering lives in the toolbar
 *  above it, so a heading is only ever a sort button. */
export const WATCH_COLUMNS: string[] = [
  "label", "source", "state", "cap", "cheapest", "last_price", "sightings", "last_seen",
];

export interface WatchFilters {
  /** Livery name, model name, skin id or model id — one box, the way the
   *  activity feeds below the table already search. */
  query: string;
  source: string;
  ownership: string;
  state: string;
}

export const NO_WATCH_FILTERS: WatchFilters = {
  query: "", source: "all", ownership: "all", state: "all",
};

export function countWatchFilters(filters: WatchFilters): number {
  return (Object.keys(NO_WATCH_FILTERS) as (keyof WatchFilters)[])
    .filter((key) => filters[key] !== NO_WATCH_FILTERS[key]).length;
}

function matchesState(watch: ShmWatch, state: string): boolean {
  if (state === "all") return true;
  if (state === "inactive") return !isLiveWatch(watch);
  return watchState(watch) === state;
}

export function filterWatches(watches: ShmWatch[], filters: WatchFilters): ShmWatch[] {
  const query = filters.query.trim().toLowerCase();
  return watches.filter((watch) =>
    matches(query, watch.label, watch.skin_id, watch.model_id)
    && matchesSource(watch, filters.source)
    && (filters.ownership === "all" || watch.is_owned === (filters.ownership === "owned"))
    && matchesState(watch, filters.state));
}

export const SIGHTING_SORTS: Record<string, SortSpec<ShmSighting>> = {
  skin_name: { label: "Livery", dir: 1, hints: ["A to Z", "Z to A"], value: (s) => s.skin_name.toLowerCase() },
  last_seen: { label: "Last seen", dir: -1, hints: ["Oldest first", "Newest first"], value: (s) => s.last_seen },
  price: { label: "Buy-now price", dir: 1, hints: ["Cheapest first", "Priciest first"], value: (s) => s.bin_price },
  time_left: { label: "Time left", dir: 1, hints: ["Ending soonest", "Ending last"], value: (s) => s.time_left_s },
  bids: { label: "Bids", dir: -1, hints: ["Fewest first", "Most first"], value: (s) => s.bids },
};

/** The coverage feed carries only `model_id`, so ordering it by the model's
 *  *name* -- the way every other list in the tab is ordered -- needs the map the
 *  tab learns from the watch labels. */
export function checkSorts(modelLabel: (id: number) => string): Record<string, SortSpec<ShmModelCheck>> {
  return {
    model: { label: "Model", dir: 1, hints: ["A to Z", "Z to A"], value: (c) => modelLabel(c.model_id).toLowerCase() },
    last_checked: { label: "Last sweep", dir: -1, hints: ["Oldest first", "Newest first"], value: (c) => c.last_checked },
    checks: { label: "Sweeps", dir: -1, hints: ["Fewest first", "Most first"], value: (c) => c.checks },
  };
}

/** A grouped decision: one row per listing, `repeats` counting how many times
 *  the watcher weighed that same listing in the window. */
export type GroupedDecision = ShmDecision & { repeats: number };

export const DECISION_SORTS: Record<string, SortSpec<GroupedDecision>> = {
  skin_name: { label: "Livery", dir: 1, hints: ["A to Z", "Z to A"], value: (d) => d.skin_name.toLowerCase() },
  bought_at: { label: "Decided at", dir: -1, hints: ["Oldest first", "Newest first"], value: (d) => d.bought_at },
  price: { label: "Buy-now price", dir: -1, hints: ["Cheapest first", "Priciest first"], value: (d) => d.bin_price },
  est_cost: { label: "Estimated cost", dir: -1, hints: ["Cheapest first", "Priciest first"], value: (d) => d.est_cost },
  repeats: { label: "Times weighed", dir: -1, hints: ["Fewest first", "Most first"], value: (d) => d.repeats },
};

/** The watcher re-weighs every live listing on every pass, so the raw ledger is
 *  the same three auctions over and over. One row per listing, carrying its
 *  newest verdict and how often it was weighed, is the same information in a
 *  form that can be read. Rows arrive newest first, so the first wins. */
export function groupDecisions(decisions: ShmDecision[]): GroupedDecision[] {
  const byAuction = new Map<number, GroupedDecision>();
  for (const decision of decisions) {
    const seen = byAuction.get(decision.auction_id);
    if (seen) seen.repeats += 1;
    else byAuction.set(decision.auction_id, { ...decision, repeats: 1 });
  }
  return [...byAuction.values()];
}

/** Every activity feed filters the way the fleet toolbar does: free text plus
 *  the one dropdown that feed cares about. `select: "all"` is the filter off. */
export interface FeedFilters {
  query: string;
  select: string;
}

export const NO_FEED_FILTERS: FeedFilters = { query: "", select: "all" };

export function countFeedFilters(filters: FeedFilters): number {
  return (filters.query.trim() ? 1 : 0) + (filters.select === NO_FEED_FILTERS.select ? 0 : 1);
}

/** Does any field the row prints contain the query? Fields are matched as text,
 *  so an auction id typed into the search box finds its own row. */
function matches(query: string, ...fields: (string | number | null | undefined)[]): boolean {
  return !query || fields.some((field) =>
    field !== null && field !== undefined && String(field).toLowerCase().includes(query));
}

export function filterSightings(
  sightings: ShmSighting[],
  filters: FeedFilters,
  modelLabel: (id: number | null) => string,
): ShmSighting[] {
  const query = filters.query.trim().toLowerCase();
  return sightings.filter((s) =>
    matches(query, s.skin_name, modelLabel(s.model_id), s.auction_id)
    && (filters.select === "all"
      || (filters.select === "ending"
        ? s.time_left_s !== null && s.time_left_s < 3600
        : (s.bids ?? 0) > 0)));
}

export function filterChecks(
  checks: ShmModelCheck[],
  filters: FeedFilters,
  modelLabel: (id: number | null) => string,
): ShmModelCheck[] {
  const query = filters.query.trim().toLowerCase();
  return checks.filter((c) =>
    matches(query, modelLabel(c.model_id), c.model_id)
    && (filters.select === "all" || Boolean(c.truncated) === (filters.select === "truncated")));
}

export function filterDecisions(
  decisions: GroupedDecision[],
  filters: FeedFilters,
  modelLabel: (id: number | null) => string,
): GroupedDecision[] {
  const query = filters.query.trim().toLowerCase();
  return decisions.filter((d) =>
    matches(query, d.skin_name, modelLabel(d.model_id), d.auction_id, d.note)
    && (filters.select === "all" || Boolean(d.dry_run) === (filters.select === "dry_run")));
}

// bun src/components/shmFilters.ts
if ((import.meta as { main?: boolean }).main) {
  const w = (over: Partial<ShmWatch>) => ({
    label: "737-700 - x", source: "manual", cheapest_seen: null, last_price_seen: null,
    max_price: null, sightings: 0, last_seen: null, model_id: null, armed: 0,
    owned_count: 0, is_owned: false, active: 1, want: 1, bought: 0, ...over,
  } as ShmWatch);
  const order = (key: string, dir: SortDir, ws: ShmWatch[]) =>
    String([...ws].sort(sortBy(WATCH_SORTS, key, dir)).map((x) => x.label));
  const ok = (cond: boolean, why: string) => { if (!cond) throw new Error(why); };

  ok(sourceBucket("shop-pack:auto") === "shop-pack:auto", "paid pack");
  ok(sourceBucket("challenge:auto") === "challenge:auto", "challenge");
  ok(sourceBucket("shop-ticket:auto") === "shop-ticket:auto", "ticket aircraft");
  ok(sourceBucket("shop-amc:auto") === "shop-amc:auto", "AM coin aircraft");
  ok(sourceBucket("shop-gold:auto") === "shop-gold:auto", "AM Gold Crew");
  ok(sourceBucket("booster:826") === "booster", "any booster id buckets together");
  ok(sourceBucket("manual") === "manual", "manual");
  ok(sourceBucket(null) === "manual", "an unset source is manual");
  ok(sourceStyle("booster:826").cls === "is-booster", "a booster carries the booster colour");

  ok(watchModel(w({ label: "737-700 - Virgo Blue" })) === "737-700", "model comes off the label");
  ok(watchLivery(w({ label: "737-700 - Virgo Blue" })) === "Virgo Blue", "livery is the rest");
  ok(watchLivery(w({ label: "No model prefix" })) === "No model prefix", "an unsplittable name is the livery");
  ok(modelNames([w({ model_id: 31, label: "737-700 - a" })]).get(31) === "737-700",
    "the id map is learned from the labels");

  ok(watchState(w({ armed: 1 })) === "armed", "an active armed watch buys");
  ok(watchState(w({})) === "observing", "an active unarmed watch observes");
  ok(watchState(w({ active: 0, bought: 1, want: 1, armed: 1 })) === "fulfilled",
    "a bought-out watch is dormant whatever its arm flag says");
  ok(watchState(w({ active: 0 })) === "retired", "a deactivated watch is retired");
  ok(watchState(w({ active: 1, bought: 1, want: 1 })) === "fulfilled",
    "want reached is dormant even while active=1, matching select_watches");
  ok(!isLiveWatch(w({ active: 0 })) && isLiveWatch(w({})), "only active rows are live");

  const priced = [w({ label: "none" }), w({ label: "hi", cheapest_seen: 9e9 }),
                  w({ label: "lo", cheapest_seen: 1e9 })];
  ok(order("cheapest", 1, priced) === "lo,hi,none", "cheapest ascending, never-seen last");
  ok(order("cheapest", -1, priced) === "hi,lo,none", "reversed too, never-seen still last");
  ok(order("priority", 1, priced) === "none,hi,lo", "an unknown key keeps the server order");

  const caps = [w({ label: "none" }), w({ label: "small", max_price: 1e9 }),
                w({ label: "big", max_price: 8e9 })];
  ok(order("cap", -1, caps) === "big,small,none", "uncapped sinks, not an infinite cap");

  const seen = [w({ label: "old", last_seen: "2026-08-01 00:00:00" }),
                w({ label: "never" }),
                w({ label: "new", last_seen: "2026-08-25 00:00:00" })];
  ok(order("last_seen", -1, seen) === "new,old,never", "newest first, never-seen last");

  const states = [w({ label: "done", active: 0, bought: 1 }), w({ label: "watch" }),
                  w({ label: "buy", armed: 1 })];
  ok(order("state", 1, states) === "buy,watch,done", "armed first, dormant last");

  // Every column the table renders must have a spec, or its header cannot sort.
  for (const key of WATCH_COLUMNS) ok(Boolean(WATCH_SORTS[key]), `no sort spec for ${key}`);

  let s = { key: "priority", dir: 1 as SortDir };
  s = nextSort(WATCH_SORTS, s, "cheapest", "priority");
  ok(s.key === "cheapest" && s.dir === 1, "first click takes the column's own direction");
  s = nextSort(WATCH_SORTS, s, "cheapest", "priority");
  ok(s.key === "cheapest" && s.dir === -1, "second click reverses it");
  s = nextSort(WATCH_SORTS, s, "cheapest", "priority");
  ok(s.key === "priority", "third click drops back to buying priority");
  s = nextSort(WATCH_SORTS, s, "sightings", "priority");
  ok(s.key === "sightings" && s.dir === -1, "listings opens most-first, not least-first");

  const pool = [
    w({ skin_id: 1, label: "737-700 - Virgo Blue", model_id: 31, source: "shop-pack:auto", armed: 1 }),
    w({ skin_id: 2, label: "A380-800 - Mexico", model_id: 19, source: "booster:826", is_owned: true, owned_count: 2 }),
    w({ skin_id: 3, label: "A380-800 - Retired One", model_id: 19, source: "challenge:auto", active: 0 }),
    w({ skin_id: 4, label: "A330-900 - EuroSong", model_id: 7, source: "booster:859", origin: "Europe" }),
  ];
  const only = (over: Partial<WatchFilters>) =>
    String(filterWatches(pool, { ...NO_WATCH_FILTERS, ...over }).map((x) => x.skin_id));
  ok(only({}) === "1,2,3,4", "no filters keeps everything");
  ok(only({ query: "virgo" }) === "1", "the search matches the livery name");
  ok(only({ query: "2" }) === "2", "and a skin id");
  ok(only({ query: "19" }) === "2,3", "the same box takes a numeric model id");
  ok(only({ query: "a380" }) === "2,3", "and a model name");
  pool[1].origin = "South America";
  ok(only({ source: "booster" }) === "2,4", "source filter buckets boosters");
  ok(only({ source: "booster|Europe" }) === "4", "and narrows to one booster");
  const menu = sourceGroups(pool);
  const booster = menu.find((g) => g.label === "Booster");
  ok(String(booster?.options.map((o) => o.value)) === "booster,booster|Europe,booster|South America",
    "booster group lists all boosters, then each set");
  ok(!menu.some((g) => g.label === "Challenge"), "a challenge with no origin stays a single option");
  ok(only({ ownership: "owned" }) === "2", "ownership filter");
  ok(only({ state: "armed" }) === "1", "state filter picks armed rows");
  ok(only({ state: "inactive" }) === "3", "and dormant rows of either kind");
  ok(only({ query: "a380", state: "armed" }) === "", "filters combine");
  ok(countWatchFilters({ ...NO_WATCH_FILTERS }) === 0, "a clean toolbar counts zero");
  ok(countWatchFilters({ ...NO_WATCH_FILTERS, query: "x", state: "armed" }) === 2, "and two when two are set");

  // Every sort a menu can offer has to name both of its directions, or the
  // option renders as a bare label the reader cannot tell apart from its twin.
  const specTables: Record<string, SortSpec<never>>[] = [
    WATCH_SORTS, SIGHTING_SORTS, DECISION_SORTS,
    checkSorts(() => "x") as Record<string, SortSpec<never>>,
  ];
  for (const specs of specTables) {
    for (const [key, spec] of Object.entries(specs)) {
      ok(spec.hints.length === 2 && spec.hints.every(Boolean), `${key} is missing a direction hint`);
    }
  }

  const sightings = [{ skin_name: "none", bin_price: null }, { skin_name: "hi", bin_price: 9e9 },
                      { skin_name: "lo", bin_price: 1e9 }] as ShmSighting[];
  ok(String([...sightings].sort(sortBy(SIGHTING_SORTS, "price", 1)).map((x) => x.skin_name))
    === "lo,hi,none", "sighting price ascending, unpriced last");
  ok(String([...sightings].sort(sortByValue(SIGHTING_SORTS, "price:-1")).map((x) => x.skin_name))
    === "hi,lo,none", "a menu value carries its own direction");
  ok(String([...sightings].sort(sortByValue(SIGHTING_SORTS, "price:1")).map((x) => x.skin_name))
    === "lo,hi,none", "and the other one too");

  const named = (id: number | null) => (id === 5 ? "A380-800" : "737-700");

  const feed = (over: Partial<FeedFilters>) => ({ ...NO_FEED_FILTERS, ...over });
  const live = [
    { auction_id: 11, skin_id: 1, model_id: 5, skin_name: "A380-800 - Mexico", time_left_s: 900, bids: 0 },
    { auction_id: 12, skin_id: 2, model_id: 3, skin_name: "737-700 - Virgo Blue", time_left_s: 7200, bids: 4 },
  ] as ShmSighting[];
  const listed = (f: Partial<FeedFilters>) =>
    String(filterSightings(live, feed(f), named).map((x) => x.auction_id));
  ok(listed({}) === "11,12", "an untouched feed filter keeps every listing");
  ok(listed({ query: "virgo" }) === "12", "the feed search matches the livery");
  ok(listed({ query: "a380" }) === "11", "and the model name the row prints");
  ok(listed({ query: "12" }) === "12", "and the auction id");
  ok(listed({ select: "ending" }) === "11", "ending soon is under an hour left");
  ok(listed({ select: "bids" }) === "12", "and the bid filter needs at least one");
  ok(listed({ query: "a380", select: "bids" }) === "", "feed filters combine");
  ok(countFeedFilters(feed({})) === 0 && countFeedFilters(feed({ query: "x", select: "ending" })) === 2,
    "the feed filter count matches the toolbar");

  // Ids ascending would read "5,30", so a name order that reads "30,5" proves
  // the column sorts by what the row actually prints.
  const checks = [{ model_id: 5, checks: 1, truncated: 1 }, { model_id: 30, checks: 9, truncated: 0 }] as ShmModelCheck[];
  const sweeps = checkSorts(named);
  ok(String([...checks].sort(sortBy(sweeps, "checks", -1)).map((x) => x.model_id))
    === "30,5", "checks: most checks first");
  ok(String([...checks].sort(sortBy(sweeps, "model", 1)).map((x) => x.model_id))
    === "30,5", "sweeps order by model name, not by model id");
  ok(String(filterChecks(checks, feed({ select: "truncated" }), named).map((x) => x.model_id)) === "5",
    "filterChecks truncated");
  ok(String(filterChecks(checks, feed({ query: "737" }), named).map((x) => x.model_id)) === "30",
    "and the sweep search takes a model name");

  const ledger = [
    { auction_id: 7, skin_name: "a", model_id: 5, bin_price: 9, bought_at: "2026-08-26 08:30:00", dry_run: 1 },
    { auction_id: 7, skin_name: "a", model_id: 5, bin_price: 9, bought_at: "2026-08-26 08:29:00", dry_run: 1 },
    { auction_id: 8, skin_name: "b", model_id: 3, bin_price: 1, bought_at: "2026-08-26 08:28:00", dry_run: 0 },
  ] as ShmDecision[];
  const grouped = groupDecisions(ledger);
  ok(grouped.length === 2, "one row per listing");
  ok(grouped[0].repeats === 2 && grouped[0].bought_at === "2026-08-26 08:30:00",
    "the newest verdict wins and carries the repeat count");
  ok(String(grouped.map((d) => d.bin_price).sort()) === "1,9", "both listings survive");
  ok(String([...grouped].sort(sortBy(DECISION_SORTS, "price", -1)).map((x) => x.bin_price))
    === "9,1", "decisions: highest price first");
  ok(filterDecisions(grouped, feed({ select: "dry_run" }), named).length === 1, "filterDecisions dry_run");
  ok(String(filterDecisions(grouped, feed({ query: "a380" }), named).map((d) => d.auction_id)) === "7",
    "and the ledger search takes a model name too");

  console.log("shmFilters self-check ok");
}
