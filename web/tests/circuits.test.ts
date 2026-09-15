import { expect, test } from "bun:test";
import { circuitIssues, classRevenue, compareCircuits, hubBoard, matchesCircuit } from "../src/components/Circuits";
import { NetworkCircuit } from "../src/types";

const circuit = (over: Partial<NetworkCircuit>): NetworkCircuit => ({
  name: "HKG-C001", hub_iata: "HKG", aircraft_model: "A380", status: "completed", total_hours: 100, waves: 2, waves_bought: 2, waves_scheduled: 2,
  seats: { eco: 1, bus: 0, fir: 0, cargo: 0 }, daily_rev: 0, weekly_rev: 100, investment: 0, route_investment: 0, updated_at: null,
  aircraft: 14, idle_aircraft: 0, avg_utilization: 100, routes_owned: 1, routes: [{ dest_iata: "NRT", dest_name: null, hub_iata: "HKG", distance_km: null, flight_time_rt: null, route_order: 0, eco_demand: null, bus_demand: null, fir_demand: null, cargo_demand: null, is_owned: true, line_id: null }],
  ...over,
});
const base = { query: "", hub: "all", status: "all", model: "all", attention: "all" };

test("attention filter separates the circuits with gaps from the healthy ones", () => {
  const healthy = circuit({});
  const gappy = circuit({ name: "HKG-C002", waves_scheduled: 1, idle_aircraft: 3 });
  expect(matchesCircuit(healthy, { ...base, attention: "healthy" })).toBe(true);
  expect(matchesCircuit(gappy, { ...base, attention: "healthy" })).toBe(false);
  expect(matchesCircuit(gappy, { ...base, attention: "gaps" })).toBe(true);
  expect(matchesCircuit(gappy, { ...base, attention: "idle" })).toBe(true);
  expect(matchesCircuit(healthy, { ...base, query: "nrt" })).toBe(true);
  expect(matchesCircuit(healthy, { ...base, model: "A350" })).toBe(false);
});

test("sorts fall back to the name so ties stay stable", () => {
  const a = circuit({ name: "HKG-C001", weekly_rev: 5 }), b = circuit({ name: "HKG-C002", weekly_rev: 5 }), c = circuit({ name: "HKG-C003", weekly_rev: 9, waves_scheduled: 0 });
  expect([b, c, a].sort(compareCircuits("weekly_desc")).map((x) => x.name)).toEqual(["HKG-C003", "HKG-C001", "HKG-C002"]);
  expect([b, c, a].sort(compareCircuits("gaps_desc")).map((x) => x.name)).toEqual(["HKG-C003", "HKG-C001", "HKG-C002"]);
  expect([b, c, a].sort(compareCircuits("name_desc")).map((x) => x.name)).toEqual(["HKG-C003", "HKG-C002", "HKG-C001"]);
});

test("class revenue multiplies price by volume and treats missing classes as zero", () => {
  expect(classRevenue({ eco: 100, bus: 300, first: 900 }, { eco: 10, bus: 2, cargo: 5 })).toEqual([1000, 600, 0, 0]);
});

test("issue badges count every gap, and the hub board only alarms on built circuits", () => {
  const healthy = circuit({});
  const broken = circuit({ name: "HKG-C002", waves_scheduled: 1, idle_aircraft: 3, routes_owned: 0 });
  const planned = circuit({ name: "CGK-C001", hub_iata: "CGK", status: "planned", aircraft: 0, waves_bought: 0, waves_scheduled: 0, routes_owned: 0, weekly_rev: 40 });
  expect(circuitIssues(healthy)).toEqual([]);
  expect(circuitIssues(broken)).toEqual(["1 unscheduled wave", "3 idle aircraft", "1 route to buy"]);
  // CGK sorts first on revenue but its one planned circuit is not "attention".
  expect(hubBoard([healthy, broken, planned])).toEqual([
    { hub: "HKG", circuits: 2, aircraft: 28, weekly: 200, issues: 1 },
    { hub: "CGK", circuits: 1, aircraft: 0, weekly: 40, issues: 0 },
  ]);
});
