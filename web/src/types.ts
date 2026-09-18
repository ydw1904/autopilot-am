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
  | "shop_amc"
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
  /** Which booster / challenge / shop pack actually carries this livery, when
   *  the catalog tables know; `source` alone only names the kind of feed. */
  origin: string | null;
  origin_detail: string | null;
  owned_count: number;
  is_owned: boolean;
  sightings: number;
  cheapest_seen: number | null;
  last_price_seen: number | null;
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

export interface HangarHubOption {
  hub_iata: string;
  hub_id: number;
}

export interface HangarAircraft {
  aircraft_id: number;
  name: string;
  model: string;
  model_id: number | null;
  model_seats_total: number | null;
  model_payload_t: number | null;
  icao_code: string | null;
  category: number | null;
  speed_kmh: number | null;
  range_km: number | null;
  max_pax: number | null;
  max_tonnage: number | null;
  gross_price: number | null;
  hub_id: number | null;
  hub_iata: string | null;
  hub_name: string | null;
  utilization: number;
  wear: number;
  age: number;
  mark: string | null;
  is_rental: boolean;
  is_frozen: boolean;
  purchased_at: string | null;
  raw_price: number | null;
  seats: { eco: number; bus: number; first: number };
  payload: number;
  skin: { id: number | null; name: string | null };
  /** Next name in this livery's series, e.g. "B742-KANGAROO25-05". Null for house liveries. */
  suggested_name: string | null;
  /** The three caps the market enforces, plus what the game itself pays. */
  sale: {
    scrap: number | null;
    bin_threshold: number | null;
    max_start_bid: number | null;
    min_start_bid: number | null;
  };
  hubs: HangarHubOption[];
}

export interface HangarLiveryOption {
  skin_id: number;
  name: string | null;
  purchased: boolean;
  price_amcoins: number;
  creator: string | null;
  is_current: boolean;
}

export interface HangarFlight {
  flight_id: number;
  line_id: number | null;
  from_iata: string | null;
  to_iata: string | null;
  departure: string | null;
  arrival: string | null;
  in_future: boolean;
}

export interface NetworkRoute {
  dest_iata: string;
  dest_name: string | null;
  hub_iata: string;
  distance_km: number | null;
  flight_time_rt: number | null;
  route_order: number;
  eco_demand: number | null;
  bus_demand: number | null;
  fir_demand: number | null;
  cargo_demand: number | null;
  is_owned: boolean;
  line_id: number | null;
}

export interface NetworkCircuit {
  name: string;
  hub_iata: string;
  aircraft_model: string;
  aircraft_icao: string | null;
  status: string;
  total_hours: number;
  waves: number;
  waves_bought: number;
  waves_scheduled: number;
  seats: { eco: number; bus: number; fir: number; cargo: number };
  daily_rev: number;
  weekly_rev: number;
  investment: number;
  route_investment: number;
  updated_at: string | null;
  aircraft: number;
  idle_aircraft: number;
  avg_utilization: number;
  routes_owned: number;
  routes: NetworkRoute[];
}

export interface NetworkLine {
  hub_iata: string;
  dest_iata: string;
  dest_name: string | null;
  dest_country: string | null;
  distance_km: number | null;
  eco_demand: number | null;
  bus_demand: number | null;
  fir_demand: number | null;
  cargo_demand: number | null;
  gross_price: number | null;
  line_id: number | null;
  is_owned: boolean;
  is_planned: boolean;
  circuits: string[];
  origin?: { lat: number; lon: number } | null;
  destination?: { lat: number; lon: number } | null;
}

export interface NetworkSnapshot {
  totals: {
    circuits: number;
    operating: number;
    planned: number;
    operating_weekly_rev: number;
    planned_weekly_rev: number;
    routes_owned: number;
    routes_known: number;
    unscheduled_waves: number;
  };
  circuits: NetworkCircuit[];
  routes: NetworkLine[];
  map_error?: string | null;
  hubs: {
    hub_iata: string;
    country_code?: string | null;
    circuits: number;
    operating: number;
    aircraft: number;
    weekly_rev: number;
    routes_known: number;
    routes_owned: number;
    location?: { lat: number; lon: number } | null;
  }[];
}

export type PriceClass = "eco" | "bus" | "first" | "cargo";

export type ClassValues = Record<PriceClass, number>;

export interface PricingRoute {
  iata: string;
  line_id: number | null;
  name: string | null;
  circuit: string | null;
  price: Partial<ClassValues>;
  audit_price: Partial<ClassValues>;
  demand: Partial<ClassValues>;
  carried: Partial<ClassValues>;
  remaining: Partial<ClassValues>;
  locked_until: string | null;
  daily_revenue: number;
  weekly_revenue: number;
}

/** One aircraft as the game's own route page lists it. */
export interface ShowlineAircraft {
  aircraft_id: number;
  model: string | null;
  name: string | null;
  in_flight: boolean;
  range_km: number | null;
  use_pct: number | null;
  cargo_t: number | null;
  seats: { total: number; eco: number; bus: number; first: number } | null;
  hub: string | null;
  /** Result over 7 days, the same figure the game prints on the card. */
  result: number | null;
  wear_pct: number | null;
  age: string | null;
}

/** Everything scraped off /network/showline — the numbers the mobile API lacks. */
export interface Showline {
  route: string;
  purchased_at: string | null;
  aircraft: number | null;
  flights_per_week: number | null;
  distance_km: number | null;
  taxes: number | null;
  categories: number[];
  departure: { iata: string; country_code: string; country: string } | null;
  arrival: { iata: string; country_code: string; country: string } | null;
  totals: { today: Record<string, number>; yesterday: Record<string, number> };
  per_class: { today: Record<string, ClassValues>; yesterday: Record<string, ClassValues> };
  history: { dates: string[]; rows: Record<string, (number | null)[]> };
  forecast: { dates: string[]; turnover: (number | null)[] | null };
  week: Record<string, number>;
  aircraft_list: ShowlineAircraft[];
}

export interface RouteDetail {
  hub_iata: string;
  dest_iata: string;
  dest_name: string | null;
  dest_country: string | null;
  distance_km: number | null;
  dest_category: number | null;
  stars: number | null;
  gross_price: number | null;
  line_id: number | null;
  is_owned: boolean;
  eco_demand: number | null;
  bus_demand: number | null;
  fir_demand: number | null;
  cargo_demand: number | null;
  /** The live `line/{id}` read; null when there is no line id or mobile is down. */
  line: {
    name: string | null;
    distance_km: number | null;
    purchase_price: number | null;
    purchased_at: string | null;
    selling_price: number | null;
    locked_until: string | null;
    is_frozen: boolean;
    incidents: number;
    incidents_grounded: number;
    price: ClassValues;
    demand: ClassValues;
    remaining: ClassValues;
    audit: { date: string | null; reliability: number | null; price: ClassValues; demand: ClassValues };
  } | null;
  error: string | null;
}

export interface PricingSnapshot {
  hub_iata: string;
  backend: string;
  routes: PricingRoute[];
  daily_revenue: number;
}

export interface OpsSnapshot {
  deliveries: {
    server_time: string | null;
    error: string | null;
    events: {
      event_id: number | null;
      type: string | null;
      label: string | null;
      aircraft_id: number | null;
      finish_at: string | null;
      am_coins_to_skip: number | null;
    }[];
  };
  daily: {
    error: string | null;
    currency_claims?: number;
    currency_offers?: number;
    wheel_available?: boolean;
    slot_games_left?: number;
  };
  freshness: { label: string; table: string; newest: string | null; rows: number }[];
  browser_connected: boolean;
  mobile_configured: boolean;
}

export type PricingMode = "ideal" | "percent" | "fill";

export interface PricingPlanRoute {
  iata: string;
  line_id: number | null;
  current: Partial<ClassValues>;
  target?: Partial<ClassValues>;
  /** "dry-run" = would change; "ok"/"fail" only appear on a live apply. */
  status: string;
  detail: string;
}

export interface PricingPlan {
  hub: string;
  hub_id: number;
  backend: string;
  mode: PricingMode;
  dry_run: boolean;
  applied: number;
  routes: PricingPlanRoute[];
  counts: Record<string, number>;
  fill_revenue?: { current: number; target: number };
}

export interface FinanceSnapshot {
  as_of: string | null;
  cash: number | null;
  mobile_calls: number | null;
  oldest_read: string | null;
  valorization: number;
  days: { date: string; flights: number; maintenance: number; salary: number; margin: number; fixed: number; structural: number }[];
  week: { flights: number; maintenance: number; salary: number; margin: number; structural: number; loans: number; rental: number; income_tax_last: number; fixed_now: number; run_rate: number };
  tax: {
    taxable: number; gross: number; credit: number; cargo_bonus: number; discount_pct: number;
    next: number; next_game: number; effective_pct: number; weekly_payment: boolean;
    brackets: { min: number; max: number | null; pct: number; tax: number }[];
  };
  cashflow: Record<"yesterday" | "today" | "tomorrow", { ca: number; flightCost: number; marketing: number; loan: number; sellBuy: number; other: number; total: number }>;
  ledger: { dates: string[]; rows: { key: string; label: string; values: number[]; total: number }[]; net: number[]; net_total: number };
  loans: { id: number; bank: string; amount: number; rate: number; interest: number; repaid: number; remaining: number; weekly: number; weeks_left: number; issued: string; ends: string; progress_pct: number }[];
  loans_total: { remaining: number; weekly: number; interest: number };
  banks: { name: string; rate: number; min_rate: number; max_rate: number; express_available: number; express_min: number; market_max: number; owed: number; weeks: [number, number]; unlocked: boolean }[];
  credit_rating: string | null;
  statements: { id: number; date: string; name: string; category: string; amount: number; line_id: number | null }[];
}
