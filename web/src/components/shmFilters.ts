import { ShmDecision, ShmModelCheck, ShmSighting, ShmWatch } from "../types";
import { splitLiveryName } from "../liveryName";

/** How the tab names, colours and icons each way a watch got on the list. The
 *  class names are the livery-tag palette from the Liveries tab, so a paid pack
 *  is the same blue in both places. */
export const SOURCE_STYLES: Record<string, { label: string; cls: string; hint?: string }> = {
  "shop-pack:auto": { label: "Paid pack", cls: "is-shop-pack" },
  "shop-ticket:auto": { label: "Ticket aircraft", cls: "is-shop-tc", hint: "costs travel cards" },
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
  label: { label: "Livery", dir: 1, value: (w) => watchLivery(w).toLowerCase() },
  source: { label: "Source", dir: 1, value: (w) => sourceStyle(w.source).label },
  state: { label: "Standing order", dir: 1, value: (w) => STATE_RANK[watchState(w)] },
  cap: { label: "Price cap", dir: -1, value: (w) => w.max_price },
  cheapest: { label: "Cheapest seen", dir: 1, value: (w) => w.cheapest_seen },
  last_price: { label: "Last price", dir: 1, value: (w) => w.last_price_seen },
  sightings: { label: "Listings", dir: -1, value: (w) => w.sightings },
  last_seen: { label: "Last seen", dir: -1, value: (w) => w.last_seen },
};

/** Column order of the watched-liveries table. Filtering lives in the toolbar
 *  above it, so a heading is only ever a sort button. */
export const WATCH_COLUMNS: string[] = [
  "label", "source", "state", "cap", "cheapest", "last_price", "sightings", "last_seen",
];

export interface WatchFilters {
  /** Livery name or skin id. */
  query: string;
  /** Model name or numeric model id. */
  model: string;
  source: string;
  ownership: string;
  state: string;
}

export const NO_WATCH_FILTERS: WatchFilters = {
  query: "", model: "", source: "all", ownership: "all", state: "all",
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
  const model = filters.model.trim().toLowerCase();
  return watches.filter((watch) =>
    (!query || watch.label.toLowerCase().includes(query) || String(watch.skin_id).includes(query))
    && (!model || String(watch.model_id ?? "").includes(model)
      || watchModel(watch).toLowerCase().includes(model))
    && (filters.source === "all" || sourceBucket(watch.source) === filters.source)
    && (filters.ownership === "all" || watch.is_owned === (filters.ownership === "owned"))
    && matchesState(watch, filters.state));
}

export const SIGHTING_SORTS: Record<string, SortSpec<ShmSighting>> = {
  last_seen: { label: "Last seen", dir: -1, value: (s) => s.last_seen },
  price: { label: "Buy-now price", dir: 1, value: (s) => s.bin_price },
  time_left: { label: "Time left", dir: 1, value: (s) => s.time_left_s },
  bids: { label: "Bids", dir: -1, value: (s) => s.bids },
};

export const CHECK_SORTS: Record<string, SortSpec<ShmModelCheck>> = {
  last_checked: { label: "Last sweep", dir: -1, value: (c) => c.last_checked },
  checks: { label: "Sweeps", dir: -1, value: (c) => c.checks },
  model_id: { label: "Model", dir: 1, value: (c) => c.model_id },
};

/** A grouped decision: one row per listing, `repeats` counting how many times
 *  the watcher weighed that same listing in the window. */
export type GroupedDecision = ShmDecision & { repeats: number };

export const DECISION_SORTS: Record<string, SortSpec<GroupedDecision>> = {
  bought_at: { label: "Decided at", dir: -1, value: (d) => d.bought_at },
  price: { label: "Buy-now price", dir: -1, value: (d) => d.bin_price },
  est_cost: { label: "Estimated cost", dir: -1, value: (d) => d.est_cost },
  repeats: { label: "Times weighed", dir: -1, value: (d) => d.repeats },
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

export function filterChecks(filter: string, checks: ShmModelCheck[]): ShmModelCheck[] {
  if (filter === "truncated") return checks.filter((c) => c.truncated);
  if (filter === "complete") return checks.filter((c) => !c.truncated);
  return checks;
}

export function filterDecisions(filter: string, decisions: GroupedDecision[]): GroupedDecision[] {
  if (filter === "dry_run") return decisions.filter((d) => d.dry_run);
  if (filter === "live") return decisions.filter((d) => !d.dry_run);
  return decisions;
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
  ];
  const only = (over: Partial<WatchFilters>) =>
    String(filterWatches(pool, { ...NO_WATCH_FILTERS, ...over }).map((x) => x.skin_id));
  ok(only({}) === "1,2,3", "no filters keeps everything");
  ok(only({ query: "virgo" }) === "1", "the search matches the livery name");
  ok(only({ query: "2" }) === "2", "and a skin id");
  ok(only({ model: "19" }) === "2,3", "the model box still takes the numeric id");
  ok(only({ model: "a380" }) === "2,3", "and now the model name too");
  ok(only({ source: "booster" }) === "2", "source filter buckets boosters");
  ok(only({ ownership: "owned" }) === "2", "ownership filter");
  ok(only({ state: "armed" }) === "1", "state filter picks armed rows");
  ok(only({ state: "inactive" }) === "3", "and dormant rows of either kind");
  ok(only({ query: "a380", state: "armed" }) === "", "filters combine");
  ok(countWatchFilters({ ...NO_WATCH_FILTERS }) === 0, "a clean toolbar counts zero");
  ok(countWatchFilters({ ...NO_WATCH_FILTERS, query: "x", state: "armed" }) === 2, "and two when two are set");

  const sightings = [{ skin_name: "none", bin_price: null }, { skin_name: "hi", bin_price: 9e9 },
                      { skin_name: "lo", bin_price: 1e9 }] as ShmSighting[];
  ok(String([...sightings].sort(sortBy(SIGHTING_SORTS, "price", 1)).map((x) => x.skin_name))
    === "lo,hi,none", "sighting price ascending, unpriced last");

  const checks = [{ model_id: 5, checks: 1 }, { model_id: 1, checks: 9 }] as ShmModelCheck[];
  ok(String([...checks].sort(sortBy(CHECK_SORTS, "checks", -1)).map((x) => x.model_id))
    === "1,5", "checks: most checks first");
  ok(filterChecks("truncated", [{ truncated: 1 }, { truncated: 0 }] as ShmModelCheck[]).length === 1,
    "filterChecks truncated");

  const ledger = [
    { auction_id: 7, bin_price: 9, bought_at: "2026-08-26 08:30:00", dry_run: 1 },
    { auction_id: 7, bin_price: 9, bought_at: "2026-08-26 08:29:00", dry_run: 1 },
    { auction_id: 8, bin_price: 1, bought_at: "2026-08-26 08:28:00", dry_run: 0 },
  ] as ShmDecision[];
  const grouped = groupDecisions(ledger);
  ok(grouped.length === 2, "one row per listing");
  ok(grouped[0].repeats === 2 && grouped[0].bought_at === "2026-08-26 08:30:00",
    "the newest verdict wins and carries the repeat count");
  ok(String(grouped.map((d) => d.bin_price).sort()) === "1,9", "both listings survive");
  ok(String([...grouped].sort(sortBy(DECISION_SORTS, "price", -1)).map((x) => x.bin_price))
    === "9,1", "decisions: highest price first");
  ok(filterDecisions("dry_run", grouped).length === 1, "filterDecisions dry_run");

  console.log("shmFilters self-check ok");
}
