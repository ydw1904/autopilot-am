import { ShmWatch } from "../types";

/** A watch's source column is free text; these are the buckets the filter offers. */
export function sourceBucket(source: string | null): string {
  if (source === "shop-pack:auto" || source === "challenge:auto") return source;
  if (source?.startsWith("booster:")) return "booster";
  return "manual";
}

// Nulls always sink: a livery never seen on the market has no price to rank by,
// so it belongs at the bottom of every price sort rather than at either extreme.
export function compareWatches(sort: string, a: ShmWatch, b: ShmWatch): number {
  const nulls = (x: number | string | null, y: number | string | null) =>
    x === null && y === null ? 0 : x === null ? 1 : y === null ? -1 : null;
  const by = (x: number | string | null, y: number | string | null, desc: boolean) =>
    nulls(x, y) ?? (x! < y! ? (desc ? 1 : -1) : x! > y! ? (desc ? -1 : 1) : 0);
  switch (sort) {
    case "label": return a.label.localeCompare(b.label);
    case "cheapest": return by(a.cheapest_seen, b.cheapest_seen, false);
    case "last_price": return by(a.last_price_seen, b.last_price_seen, false);
    case "cap": return by(a.max_price, b.max_price, true);
    case "sightings": return b.sightings - a.sightings;
    case "last_seen": return by(a.last_seen, b.last_seen, true);
    default: return 0;  // "priority" keeps the server's owned-last ordering
  }
}

// bun src/components/shmFilters.ts
if ((import.meta as { main?: boolean }).main) {
  const w = (over: Partial<ShmWatch>) => ({
    label: "x", source: "manual", cheapest_seen: null, last_price_seen: null,
    max_price: null, sightings: 0, last_seen: null, ...over,
  } as ShmWatch);
  const order = (sort: string, ws: ShmWatch[]) =>
    String([...ws].sort((a, b) => compareWatches(sort, a, b)).map((x) => x.label));
  const ok = (cond: boolean, why: string) => { if (!cond) throw new Error(why); };

  ok(sourceBucket("shop-pack:auto") === "shop-pack:auto", "paid pack");
  ok(sourceBucket("challenge:auto") === "challenge:auto", "challenge");
  ok(sourceBucket("booster:826") === "booster", "any booster id buckets together");
  ok(sourceBucket("manual") === "manual", "manual");
  ok(sourceBucket(null) === "manual", "an unset source is manual");

  const priced = [w({ label: "none" }), w({ label: "hi", cheapest_seen: 9e9 }),
                  w({ label: "lo", cheapest_seen: 1e9 })];
  ok(order("cheapest", priced) === "lo,hi,none", "cheapest ascending, never-seen last");
  ok(order("priority", priced) === "none,hi,lo", "priority keeps the server order");

  const caps = [w({ label: "none" }), w({ label: "small", max_price: 1e9 }),
                w({ label: "big", max_price: 8e9 })];
  ok(order("cap", caps) === "big,small,none", "uncapped sinks, not an infinite cap");

  const seen = [w({ label: "old", last_seen: "2026-08-01 00:00:00" }),
                w({ label: "never" }),
                w({ label: "new", last_seen: "2026-08-25 00:00:00" })];
  ok(order("last_seen", seen) === "new,old,never", "newest first, never-seen last");
  console.log("shmFilters self-check ok");
}
