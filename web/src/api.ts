import { FleetAircraft, FleetStats, LiveryItem } from "./types";

const BASE_URL = "";

export async function fetchStats(): Promise<FleetStats> {
  const res = await fetch(`${BASE_URL}/api/stats`);
  if (!res.ok) throw new Error("Failed to load fleet stats");
  return res.json();
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
  if (params.sort_by) q.set("sort_by", params.sort_by);
  if (params.limit) q.set("limit", params.limit.toString());
  if (params.offset) q.set("offset", params.offset.toString());

  const res = await fetch(`${BASE_URL}/api/fleet?${q.toString()}`);
  if (!res.ok) throw new Error("Failed to load fleet aircraft");
  return res.json();
}

export interface LiveryParams {
  status_filter?: "owned" | "unowned" | "all";
  rarity?: number | null;
  model_query?: string;
  search_query?: string;
}

export async function fetchLiveries(params: LiveryParams = {}): Promise<LiveryItem[]> {
  const q = new URLSearchParams();
  if (params.status_filter && params.status_filter !== "all") {
    q.set("status_filter", params.status_filter);
  }
  if (params.rarity !== undefined && params.rarity !== null) {
    q.set("rarity", params.rarity.toString());
  }
  if (params.model_query) q.set("model_query", params.model_query);
  if (params.search_query) q.set("search_query", params.search_query);

  const res = await fetch(`${BASE_URL}/api/liveries?${q.toString()}`);
  if (!res.ok) throw new Error("Failed to load liveries");
  return res.json();
}

export async function triggerSyncFleet(hub?: string): Promise<{ status: string; message: string; count?: number }> {
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
