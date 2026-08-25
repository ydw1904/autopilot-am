export interface FleetAircraft {
  aircraft_id: number;
  name: string;
  model: string;
  utilization: number;
  hub_iata: string;
  updated_at: string;
  purchased_at?: string | null;
  skin_id: number | null;
  skin_img: string | null;
  category?: number;
  speed_kmh?: number;
  max_pax?: number;
  max_tonnage?: number;
  gross_price?: number;
  icao_code?: string;
  skin_name?: string | null;
  skin_picture_path?: string | null;
  seats_eco?: number | null;
  seats_bus?: number | null;
  seats_first?: number | null;
  payload_t?: number | null;
  ac_type?: string | null;
  haul?: HaulClass | null;
  is_cargo?: number;
  tags: string[];
}

export type HaulClass = "short" | "medium" | "long";

export type HaulTab = HaulClass | "all" | "cargo";

export interface DailyLivery {
  skin_id: number;
  name: string;
  picture_path: string | null;
  fleet_count: number;
  has_img: number;
  day: string;
  sample_aircraft: {
    aircraft_id: number;
    name: string;
    model: string;
    hub_iata: string;
    utilization: number;
  } | null;
  /** Every hub flying this livery, busiest first. */
  hubs: { hub_iata: string; count: number }[];
}

export interface LiveryPlane {
  aircraft_id: number;
  name: string;
  model: string;
  hub: string;
  country_code?: string | null;
  utilization: number;
}

// How a livery can be obtained. A livery routinely has several (the Copa
// challenge planes are also sold as travel-card offers), so the card renders
// one chip per tag; `kind` picks its icon and colour.
export type LiveryTagKind =
  | "challenge"
  | "booster"
  | "shop_gift"
  | "shop_ad"
  | "shop_tc"
  | "shop_pack"
  | "dutyfree"
  | "market";

export interface LiveryTag {
  kind: LiveryTagKind;
  label: string;
  title: string;
}

export interface LiveryItem {
  skin_id: number;
  name: string;
  model_id: number | null;
  picture_path: string | null;
  boosters: string | null;
  tags: LiveryTag[];
  fleet_count: number;
  mobile_count: number;
  owned_count: number;
  is_owned: boolean;
  is_user_created: boolean;
  source: string | null;
  has_img: number;
  // When a scrape first stored this livery locally (SQLite 'YYYY-MM-DD HH:MM:SS',
  // UTC), not the game's release date.
  first_seen: string | null;
  last_seen: string | null;
  aircraft: LiveryPlane[];
  aircraft_names: string[];
}

export interface HubCount {
  hub_iata: string;
  count: number;
  idle: number;
  country_code?: string | null;
}

export interface ModelCount {
  model: string;
  count: number;
  idle: number;
}

export interface HaulCounts {
  all: number;
  short: number;
  medium: number;
  long: number;
  cargo: number;
  unknown: number;
}

export interface FleetStats {
  total: number;
  idle: number;
  active: number;
  avg_utilization: number;
  special_skin_count: number;
  hubs: HubCount[];
  models: ModelCount[];
  hauls?: HaulCounts;
  tags: AircraftTagCount[];
}

export interface AircraftTagCount {
  tag: string;
  count: number;
}

export type AlertTone = "critical" | "warning" | "info";

export interface CommandAlert {
  id: string;
  tone: AlertTone;
  title: string;
  detail: string;
  target: "command" | "fleet" | "system";
  preset?: string;
}

export interface PipelineStage {
  key: "planned" | "bought" | "completed";
  label: string;
  count: number;
  weekly_rev: number;
}

export interface CommandHub extends HubCount {
  avg_utilization: number;
  circuits: number;
  weekly_rev: number;
}

export interface CommandCenterSnapshot {
  status: {
    browser_connected: boolean;
    mobile_configured: boolean;
    fleet_last_synced: string | null;
  };
  portfolio: {
    fleet_total: number;
    active: number;
    idle: number;
    avg_utilization: number;
    planned_weekly_rev: number;
    operating_weekly_rev: number;
  };
  pipeline: PipelineStage[];
  alerts: CommandAlert[];
  hubs: CommandHub[];
  fleet_facets: {
    hubs: HubCount[];
    models: ModelCount[];
    hauls: HaulCounts;
  };
  data_health: {
    oldest_fleet_record: string | null;
    newest_fleet_record: string | null;
    stale_aircraft: number;
    routes: number;
    owned_routes: number;
    market_watches: number;
  };
}

export interface ShmWatch {
  skin_id: number;
  model_id: number | null;
  label: string;
  max_price: number | null;
  want: number;
  bought: number;
  active: number;
  armed: number;
  source: string | null;
  owned_count: number;
  is_owned: boolean;
  sightings: number;
  cheapest_seen: number | null;
  last_seen: string | null;
}

export interface ShmModelCheck {
  model_id: number;
  last_checked: string | null;
  checks: number;
  truncated: number;
}

export interface ShmSighting {
  auction_id: number;
  skin_id: number;
  model_id: number | null;
  skin_name: string;
  current_price: number | null;
  bin_price: number | null;
  time_left_s: number | null;
  bids: number | null;
  first_seen: string | null;
  last_seen: string | null;
}

export interface ShmDecision {
  buy_id: number;
  auction_id: number;
  skin_id: number;
  model_id: number | null;
  skin_name: string;
  bin_price: number;
  est_cost: number;
  dry_run: number;
  confirmed: number | null;
  note: string | null;
  bought_at: string;
}

export interface ShmMonitorSnapshot {
  status: {
    observing: boolean;
    last_activity: string | null;
  };
  summary: {
    active_watches: number;
    paid_pack_watches: number;
    armed_watches: number;
    watched_models: number;
    matched_sightings: number;
    real_buys_today: number;
    dry_runs_today: number;
  };
  watches: ShmWatch[];
  model_checks: ShmModelCheck[];
  sightings: ShmSighting[];
  decisions: ShmDecision[];
}

export interface FleetPage {
  items: FleetAircraft[];
  total: number;
  limit: number;
  offset: number;
}
