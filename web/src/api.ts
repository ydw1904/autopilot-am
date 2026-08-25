import {
  CommandCenterSnapshot,
  DailyLivery,
  FleetAircraft,
  FleetPage,
  FleetStats,
  HaulTab,
  LiveryItem,
  ShmMonitorSnapshot,
} from "./types";

const BASE_URL = "";

export async function fetchStats(): Promise<FleetStats> {
  const res = await fetch(`${BASE_URL}/api/stats`);
  if (!res.ok) throw new Error("Failed to load fleet stats");
  return res.json();
}

export async function fetchCommandCenter(): Promise<CommandCenterSnapshot> {
  const res = await fetch(`${BASE_URL}/api/command-center`);
  if (!res.ok) throw new Error("Failed to load command center");
  return res.json();
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

export interface FleetParams {
  hubs?: string[];
  models?: string[];
  min_util?: number;
  max_util?: number;
  name_query?: string;
  model_query?: string;
  skin_filter?: "special" | "manufacturer" | "all";
  skin_id?: number | null;
  haul?: HaulTab;
  tag?: string;
  sort_by?: string;
  limit?: number;
  offset?: number;
}

export async function fetchFleet(params: FleetParams = {}): Promise<FleetAircraft[]> {
  const q = new URLSearchParams();
  if (params.hubs && params.hubs.length > 0) {
    q.set("hubs", params.hubs.join(","));
  }
  if (params.min_util !== undefined) q.set("min_util", params.min_util.toString());
  if (params.max_util !== undefined) q.set("max_util", params.max_util.toString());
  if (params.name_query) q.set("name_query", params.name_query);
  if (params.model_query) q.set("model_query", params.model_query);
  if (params.skin_filter && params.skin_filter !== "all") {
    q.set("skin_filter", params.skin_filter);
  }
  if (params.skin_id) q.set("skin_id", params.skin_id.toString());
  if (params.haul && params.haul !== "all") q.set("haul", params.haul);
  if (params.tag && params.tag !== "all") q.set("tag", params.tag);
  if (params.sort_by) q.set("sort_by", params.sort_by);
  if (params.limit) q.set("limit", params.limit.toString());
  if (params.offset) q.set("offset", params.offset.toString());

  const res = await fetch(`${BASE_URL}/api/fleet?${q.toString()}`);
  if (!res.ok) throw new Error("Failed to load fleet aircraft");
  return res.json();
}

export interface FleetPageParams {
  q?: string;
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

export async function bulkRenameAircraft(
  aircraftIds: number[],
  prefix: string,
  addNumbering: boolean
): Promise<{ ok: number; failed: number }> {
  const res = await fetch(`${BASE_URL}/api/rename`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      aircraft_ids: aircraftIds,
      prefix,
      add_numbering: addNumbering,
    }),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || "Bulk rename failed");
  }
  return res.json();
}

export async function assignToCircuit(
  aircraftIds: number[],
  circuitCode: string
): Promise<{ ok: number; failed: number }> {
  const res = await fetch(`${BASE_URL}/api/assign-circuit`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      aircraft_ids: aircraftIds,
      circuit_code: circuitCode,
    }),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || "Assign circuit failed");
  }
  return res.json();
}
