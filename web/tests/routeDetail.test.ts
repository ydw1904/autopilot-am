import { expect, test } from "bun:test";
import { dailyCapacity } from "../src/classes";
import { NetworkCircuit } from "../src/types";

const circuit = (over: Partial<NetworkCircuit>): NetworkCircuit => ({
  name: "FRA-C001", hub_iata: "FRA", aircraft_model: "747", status: "completed", total_hours: 160,
  waves: 2, waves_bought: 2, waves_scheduled: 2, seats: { eco: 300, bus: 20, fir: 5, cargo: 10 },
  daily_rev: 0, weekly_rev: 0, investment: 0, route_investment: 0, updated_at: null, aircraft: 14,
  idle_aircraft: 0, avg_utilization: 100, routes_owned: 1, routes: [], ...over,
});

test("daily capacity is both directions once per wave, summed over every circuit on the route", () => {
  expect(dailyCapacity([circuit({})])).toEqual({ eco: 1200, bus: 80, first: 20, cargo: 40 });
  // A route shared by two circuits is served by both of them.
  expect(dailyCapacity([circuit({}), circuit({ name: "FRA-C002", waves: 1 })]))
    .toEqual({ eco: 1800, bus: 120, first: 30, cargo: 60 });
  expect(dailyCapacity([])).toEqual({ eco: 0, bus: 0, first: 0, cargo: 0 });
});
