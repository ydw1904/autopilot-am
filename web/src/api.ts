import {
  CommandCenterSnapshot,
  DailyLivery,
  FinanceSnapshot,
  FleetPage,
  FleetStats,
  HangarAircraft,
  HangarFlight,
  HangarLiveryOption,
  HaulTab,
  LiveryItem,
  NetworkSnapshot,
  OpsSnapshot,
  PricingMode,
  PricingPlan,
  PricingSnapshot,
  RouteDetail,
  Showline,
  ShmMonitorSnapshot,
} from "./types";

const BASE_URL = "";

// Switching views unmounts the old one, so every view refetches its snapshot
// from scratch on mount -- and /api/ops is ~3.5s of live game calls, /api/network
// a 700 KB read. Hand back the in-flight (or recent) promise per URL instead, so
// flipping between tabs is free. Cleared by clearApiCache() on any write or an
// explicit refresh; the TTL is the backstop for a tab left open all afternoon.
const SNAPSHOT_TTL_MS = 30_000;
// Live pricing is one mobile call per hub -- 23 of them to fill the Network
// table -- so it keeps its entry far longer than a plain snapshot. A write or
// the refresh button still drops it, which is what actually makes it stale.
const PRICING_TTL_MS = 15 * 60_000;
const snapshots = new Map<string, { at: number; promise: Promise<unknown> }>();

export function clearApiCache(): void {
  snapshots.clear();
}

function cachedGet<T>(path: string, failure: string, ttl = SNAPSHOT_TTL_MS): Promise<T> {
  const hit = snapshots.get(path);
  if (hit && Date.now() - hit.at < ttl) return hit.promise as Promise<T>;

  const promise = fetch(`${BASE_URL}${path}`).then(async (res) => {
    if (!res.ok) {
      const detail = (await res.json().catch(() => null))?.detail;
      throw new Error(typeof detail === "string" ? detail : failure);
    }
    return res.json();
  });
  // A failed load must never be served from the cache.
  promise.catch(() => { if (snapshots.get(path)?.promise === promise) snapshots.delete(path); });
  snapshots.set(path, { at: Date.now(), promise });
  return promise as Promise<T>;
}

export async function fetchStats(): Promise<FleetStats> {
  const res = await fetch(`${BASE_URL}/api/stats`);
  if (!res.ok) throw new Error("Failed to load fleet stats");
  return res.json();
}

export function fetchCommandCenter(): Promise<CommandCenterSnapshot> {
  return cachedGet("/api/command-center", "Failed to load command center");
}

export async function fetchShmMonitor(): Promise<ShmMonitorSnapshot> {
  const res = await fetch(`${BASE_URL}/api/shm-monitor`);
  if (!res.ok) throw new Error("Failed to load SHM watcher activity");
  return res.json();
}

export async function updateShmWatch(
  skinId: number,
  patch: { armed?: boolean; max_price?: number | null },
): Promise<void> {
  const res = await fetch(`${BASE_URL}/api/shm-monitor/watches/${skinId}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(patch),
  });
  if (!res.ok) throw new Error("Failed to update SHM watch");
}

export async function fetchFleetNameSuggestions(term: string): Promise<string[]> {
  const res = await fetch(`${BASE_URL}/api/fleet-name-suggestions?q=${encodeURIComponent(term)}`);
  if (!res.ok) throw new Error("Failed to load aircraft name suggestions");
  return res.json();
}

export interface FleetPageParams {
  q?: string;
  name_query?: string;
  hub?: string;
  utilization?: "all" | "active" | "idle" | "partial" | "full";
  skin_filter?: "special" | "manufacturer" | "all";
  haul?: HaulTab;
  tag?: string;
  sort_by?: string;
  limit?: number;
  offset?: number;
}

export async function fetchFleetPage(params: FleetPageParams = {}): Promise<FleetPage> {
  const q = new URLSearchParams();
  if (params.q) q.set("q", params.q);
  if (params.name_query) q.set("name_query", params.name_query);
  if (params.hub && params.hub !== "all") q.set("hubs", params.hub);
  if (params.haul && params.haul !== "all") q.set("haul", params.haul);
  if (params.tag && params.tag !== "all") q.set("tag", params.tag);
  if (params.skin_filter && params.skin_filter !== "all") q.set("skin_filter", params.skin_filter);
  if (params.sort_by) q.set("sort_by", params.sort_by);
  if (params.limit) q.set("limit", String(params.limit));
  if (params.offset) q.set("offset", String(params.offset));

  if (params.utilization === "idle") {
    q.set("min_util", "0");
    q.set("max_util", "0");
  } else if (params.utilization === "active") {
    q.set("min_util", "0.01");
  } else if (params.utilization === "partial") {
    q.set("min_util", "0.01");
    q.set("max_util", "99.99");
  } else if (params.utilization === "full") {
    q.set("min_util", "100");
    q.set("max_util", "100");
  }

  const res = await fetch(`${BASE_URL}/api/fleet-page?${q.toString()}`);
  if (!res.ok) throw new Error("Failed to load fleet page");
  return res.json();
}

export async function updateAircraftTags(
  aircraftIds: number[],
  changes: { add?: string[]; remove?: string[] },
): Promise<{ aircraft: { aircraft_id: number; tags: string[] }[] }> {
  const res = await fetch(`${BASE_URL}/api/fleet/tags`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ aircraft_ids: aircraftIds, add: changes.add || [], remove: changes.remove || [] }),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || "Failed to update aircraft tags");
  }
  return res.json();
}

export async function fetchAircraftPurchaseDate(aircraftId: number): Promise<{
  aircraft_id: number;
  purchased_at: string;
  cached: boolean;
}> {
  const res = await fetch(`${BASE_URL}/api/fleet/${aircraftId}/purchase-date`);
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || "Failed to load purchase date");
  }
  return res.json();
}

export interface LiveryParams {
  status_filter?: "owned" | "unowned" | "all";
  model_query?: string;
  search_query?: string;
  include_user_created?: boolean;
}

export async function fetchLiveries(params: LiveryParams = {}): Promise<LiveryItem[]> {
  const q = new URLSearchParams();
  if (params.status_filter && params.status_filter !== "all") {
    q.set("status_filter", params.status_filter);
  }
  if (params.model_query) q.set("model_query", params.model_query);
  if (params.search_query) q.set("search_query", params.search_query);
  if (params.include_user_created === false) q.set("include_user_created", "false");

  const res = await fetch(`${BASE_URL}/api/liveries?${q.toString()}`);
  if (!res.ok) throw new Error("Failed to load liveries");
  return res.json();
}

export async function triggerSyncLiveries(): Promise<{
  status: string;
  message: string;
  new_liveries: number;
  images_fetched: number;
  booster_skins: number;
  shop_skins: number;
  challenge_skins: number;
}> {
  const res = await fetch(`${BASE_URL}/api/sync-liveries`, { method: "POST" });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || "Livery catalog sync failed");
  }
  return res.json();
}

export async function fetchDailyLiveries(count = 3, seed?: string): Promise<DailyLivery[]> {
  const query = seed ? `&seed=${encodeURIComponent(seed)}` : "";
  const res = await fetch(`${BASE_URL}/api/liveries/daily?count=${count}${query}`);
  if (!res.ok) throw new Error("Failed to load liveries of the day");
  return res.json();
}

export async function launchBrowser(): Promise<{
  status: string;
  browser_connected: boolean;
  logged_in: boolean;
  usable?: boolean;
  message: string;
}> {
  const res = await fetch(`${BASE_URL}/api/launch-browser`, { method: "POST" });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || "Failed to launch browser");
  }
  return res.json();
}

export interface PurchaseBackfillStatus {
  state: "idle" | "running" | "done" | "stopped" | "error";
  running: boolean;
  done: number;
  failed: number;
  total: number;
  pending: number | null;
  eta_minutes?: number;
  message: string | null;
}

export async function fetchPurchaseBackfillStatus(): Promise<PurchaseBackfillStatus> {
  const res = await fetch(`${BASE_URL}/api/fleet/purchase-dates`);
  if (!res.ok) throw new Error("Failed to read the purchase-date backfill status");
  return res.json();
}

export async function triggerSyncFleet(hub?: string): Promise<{
  status: string;
  source: "mobile" | "cdp_fallback";
  message: string;
  count?: number;
  purchase_backfill?: PurchaseBackfillStatus | null;
}> {
  const res = await fetch(`${BASE_URL}/api/sync-fleet`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ hub: hub || null }),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || "Fleet sync request failed");
  }
  return res.json();
}

// ── Hangar: one aircraft, every write the mobile API can make against it ────

async function hangarPost<T>(path: string, body?: unknown): Promise<T> {
  const res = await fetch(`${BASE_URL}/api/hangar/${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body ?? {}),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || "Hangar action failed");
  }
  return res.json();
}

async function hangarGet<T>(path: string, what: string): Promise<T> {
  const res = await fetch(`${BASE_URL}/api/hangar/${path}`);
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || `Failed to load ${what}`);
  }
  return res.json();
}

export function fetchHangarAircraft(aircraftId: number): Promise<HangarAircraft> {
  return hangarGet(`${aircraftId}`, "the aircraft");
}

export function fetchHangarLiveries(aircraftId: number): Promise<{
  aircraft_id: number;
  current: { id: number | null; name: string | null };
  liveries: HangarLiveryOption[];
}> {
  return hangarGet(`${aircraftId}/liveries`, "the livery options");
}

export function fetchHangarSchedule(aircraftId: number, day: number): Promise<{
  aircraft_id: number;
  day: number;
  flights: HangarFlight[];
}> {
  return hangarGet(`${aircraftId}/schedule?day=${day}`, "the schedule");
}

export function renameHangarAircraft(aircraftId: number, name: string): Promise<HangarAircraft> {
  return hangarPost(`${aircraftId}/name`, { name });
}

export function reconfigureHangarAircraft(
  aircraftId: number,
  seats: { eco: number; bus: number; first: number; payload: number },
): Promise<HangarAircraft> {
  return hangarPost(`${aircraftId}/seats`, seats);
}

export function moveHangarAircraft(aircraftId: number, hubIata: string): Promise<HangarAircraft> {
  return hangarPost(`${aircraftId}/hub`, { hub_iata: hubIata });
}

export function paintHangarAircraft(
  aircraftId: number,
  skinId: number,
  confirmOverwrite = false,
): Promise<HangarAircraft> {
  return hangarPost(`${aircraftId}/livery`, { skin_id: skinId, confirm_overwrite: confirmOverwrite });
}

export function listHangarAircraft(
  aircraftId: number,
  sale: { bin_price: number; price?: number; duration?: number },
): Promise<{ auction_id: number; bin_price: number; fair_value: number; time_left_s: number }> {
  return hangarPost(`${aircraftId}/sell`, sale);
}

export function scrapHangarAircraft(
  aircraftId: number,
  confirmName: string,
): Promise<{ aircraft_id: number; name: string; scrapped_for: number | null; message: string }> {
  return hangarPost(`${aircraftId}/scrap`, { confirm_name: confirmName });
}

export function unscheduleHangarAircraft(aircraftId: number): Promise<{ cleared: boolean }> {
  return hangarPost(`${aircraftId}/unschedule`);
}

export function fetchNetwork(): Promise<NetworkSnapshot> {
  return cachedGet("/api/network", "Failed to load the circuit network");
}

export function fetchPricing(hub: string, backend?: "mobile" | "cdp"): Promise<PricingSnapshot> {
  const query = backend ? `?backend=${backend}` : "";
  return cachedGet(`/api/pricing/${encodeURIComponent(hub)}${query}`, "Failed to load live prices", PRICING_TTL_MS);
}

export function fetchRouteDetail(hub: string, dest: string): Promise<RouteDetail> {
  return cachedGet(`/api/route/${encodeURIComponent(hub)}/${encodeURIComponent(dest)}`, "Failed to load the route");
}

/** The /network/showline scrape. Separate from fetchRouteDetail because it is
 *  the only part that needs a signed-in Chrome, and ~1.2s of CDP. */
export function fetchRouteShowline(hub: string, dest: string): Promise<{ details: Showline | null; error: string | null }> {
  return cachedGet(`/api/route/${encodeURIComponent(hub)}/${encodeURIComponent(dest)}/details`, "Failed to load the route details page");
}

/** The server keeps each finance read until the game can have changed it;
 *  `refresh` (the Reload button) makes it re-read everything. */
export function fetchFinance(refresh = false): Promise<FinanceSnapshot> {
  return cachedGet(`/api/finance${refresh ? "?refresh=true" : ""}`, "Failed to load finances");
}

export function fetchOps(): Promise<OpsSnapshot> {
  return cachedGet("/api/ops", "Failed to load operations status");
}

export async function applyPricing(
  hub: string,
  body: { mode: PricingMode; pct?: number; routes?: string[]; circuit?: string; dry_run: boolean },
): Promise<PricingPlan> {
  const res = await fetch(`${BASE_URL}/api/pricing/${encodeURIComponent(hub)}/apply`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    const detail = (await res.json().catch(() => null))?.detail;
    throw new Error(typeof detail === "string" ? detail : "Pricing run failed");
  }
  return res.json();
}
