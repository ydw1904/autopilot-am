export interface FleetAircraft {
  aircraft_id: number;
  name: string;
  model: string;
  utilization: number;
  hub_iata: string;
  updated_at: string;
  skin_id: number | null;
  skin_img: string | null;
  category?: number;
  speed_kmh?: number;
  range_km?: number;
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
  wear?: number | null;
  ac_type?: string | null;
  haul?: HaulClass | null;
  is_cargo?: number;
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
}

export interface LiveryPlane {
  aircraft_id: number;
  name: string;
  model: string;
  hub: string;
  utilization: number;
}

export interface LiveryItem {
  skin_id: number;
  name: string;
  model_id: number | null;
  picture_path: string | null;
  boosters: string | null;
  fleet_count: number;
  mobile_count: number;
  owned_count: number;
  is_owned: boolean;
  has_img: number;
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
}
