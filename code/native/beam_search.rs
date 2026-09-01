//! Native circuit-planner optimizers exposed through a small C ABI.

use std::cmp::Ordering;
use std::collections::{HashMap, HashSet};
use std::ffi::{c_double, c_int};
use std::os::raw::c_longlong;

const MAX_ROUTES: usize = 192;

#[derive(Clone, Copy, Eq, Hash, PartialEq)]
struct RouteSet([u64; 3]);

impl RouteSet {
    fn contains(self, index: usize) -> bool {
        self.0[index / 64] & (1_u64 << (index % 64)) != 0
    }

    fn with(mut self, index: usize) -> Self {
        self.0[index / 64] |= 1_u64 << (index % 64);
        self
    }

    fn indices(self) -> Vec<usize> {
        let mut out = Vec::new();
        for (word_index, mut bits) in self.0.into_iter().enumerate() {
            while bits != 0 {
                out.push(word_index * 64 + bits.trailing_zeros() as usize);
                bits &= bits - 1;
            }
        }
        out
    }
}

#[derive(Clone, Copy)]
struct BeamState {
    time_used: f64,
    routes: RouteSet,
    min_eco: f64,
    max_eco: f64,
    min_cargo: f64,
    max_cargo: f64,
}

#[derive(Clone, Copy)]
struct ResultRow {
    score: f64,
    time: f64,
    routes: RouteSet,
}

fn eval_config(
    seats: [i32; 4],
    indices: &[usize],
    demands: &[f64],
    prices: &[f64],
    max_pax: f64,
    max_ton: f64,
    max_waves: usize,
    overshoot_pct: f64,
) -> f64 {
    let [eco, bus, first, cargo] = seats;
    if eco + bus + first + cargo == 0 {
        return 0.0;
    }
    if eco as f64 * 0.1 + bus as f64 * 0.125 + first as f64 * 0.15 + cargo as f64 > max_ton
        || eco as f64 + bus as f64 * 1.8 + first as f64 * 4.2 > max_pax
    {
        return 0.0;
    }

    let tolerance = if overshoot_pct > 0.0 {
        1.0 + overshoot_pct
    } else {
        1.0
    };
    let mut wave_limit = max_waves as f64;
    for &index in indices {
        let base = index * 4;
        for class in 0..4 {
            let seat_count = seats[class];
            if seat_count <= 0 {
                continue;
            }
            let demand = demands[base + class];
            if demand <= 0.0 {
                return 0.0;
            }
            let allowed = if class == 0 {
                demand
            } else {
                demand * tolerance
            };
            wave_limit = wave_limit.min(allowed / (2.0 * seat_count as f64));
        }
    }
    let waves = wave_limit.floor() as usize;
    if waves < 1 {
        return 0.0;
    }

    let mut revenue = 0.0;
    for &index in indices {
        let base = index * 4;
        for class in 0..4 {
            let seat_count = seats[class];
            if seat_count <= 0 {
                continue;
            }
            let demand = demands[base + class];
            let price = prices[base + class];
            let capacity = 2.0 * seat_count as f64 * waves as f64;
            if price == 0.0 || demand == 0.0 {
                continue;
            }
            let sale_price = if capacity < demand {
                (price * (1.0 - (capacity - demand) / (3.0 * demand))).floor()
            } else {
                price
            };
            revenue += demand.min(capacity) * sale_price;
        }
    }
    revenue
}

fn quick_score(
    indices: &[usize],
    demands: &[f64],
    prices: &[f64],
    max_pax: f64,
    max_ton: f64,
    max_waves: usize,
    overshoot_pct: f64,
) -> f64 {
    if indices.is_empty() {
        return 0.0;
    }
    let mut minimum = [f64::INFINITY; 4];
    for &index in indices {
        for class in 0..4 {
            minimum[class] = minimum[class].min(demands[index * 4 + class]);
        }
    }

    let mut best: f64 = 0.0;
    for target_waves in [3.0, 5.0, 8.0, 10.0, 15.0, 20.0] {
        for flags in 0..4 {
            let first = if flags & 2 != 0 {
                (minimum[2] / (2.0 * target_waves)) as i32
            } else {
                0
            }
            .max(0);
            let bus = if flags & 1 != 0 {
                (minimum[1] / (2.0 * target_waves)) as i32
            } else {
                0
            }
            .max(0);
            let cargo = ((minimum[3] / (2.0 * target_waves)) as i32).max(0);
            let eco_demand = ((minimum[0] / (2.0 * target_waves)) as i32).max(0);
            let eco_payload = (((max_ton - bus as f64 * 0.125 - first as f64 * 0.15 - cargo as f64)
                / 0.1) as i32)
                .max(0);
            let eco_space = ((max_pax - bus as f64 * 1.8 - first as f64 * 4.2) as i32).max(0);
            let eco = eco_demand.min(eco_payload).min(eco_space);
            best = best.max(eval_config(
                [eco, bus, first, cargo],
                indices,
                demands,
                prices,
                max_pax,
                max_ton,
                max_waves,
                overshoot_pct,
            ));
        }
    }
    best
}

#[allow(clippy::too_many_arguments)]
fn beam_search(
    demands: &[f64],
    prices: &[f64],
    flight_times: &[f64],
    eco_demands: &[f64],
    cargo_demands: &[f64],
    top_indices: &[i64],
    max_pax: f64,
    max_ton: f64,
    max_waves: usize,
    top_n: usize,
    beam_width: usize,
    max_steps: usize,
    match_ratio: f64,
    overshoot_pct: f64,
) -> Vec<ResultRow> {
    let mut beam = vec![BeamState {
        time_used: 0.0,
        routes: RouteSet([0; 3]),
        min_eco: f64::INFINITY,
        max_eco: 0.0,
        min_cargo: f64::INFINITY,
        max_cargo: 0.0,
    }];
    let mut best = Vec::with_capacity(top_n + 1);
    let mut seen = HashSet::new();
    let mut score_cache = HashMap::new();

    for _ in 0..max_steps {
        let mut next_beam = Vec::new();
        for state in &beam {
            for &raw_index in top_indices {
                let index = raw_index as usize;
                if state.routes.contains(index) {
                    continue;
                }
                let new_time = state.time_used + flight_times[index];
                if new_time > 168.0 {
                    continue;
                }
                let min_eco = state.min_eco.min(eco_demands[index]);
                let max_eco = state.max_eco.max(eco_demands[index]);
                let min_cargo = state.min_cargo.min(cargo_demands[index]);
                let max_cargo = state.max_cargo.max(cargo_demands[index]);
                if max_eco > 0.0 && min_eco / max_eco < match_ratio
                    || max_cargo > 0.0 && min_cargo / max_cargo < match_ratio
                {
                    continue;
                }
                let routes = state.routes.with(index);
                if !seen.insert(routes) {
                    continue;
                }
                let score = *score_cache.entry(routes).or_insert_with(|| {
                    let indices = routes.indices();
                    if indices.len() >= 2 {
                        quick_score(
                            &indices,
                            demands,
                            prices,
                            max_pax,
                            max_ton,
                            max_waves,
                            overshoot_pct,
                        )
                    } else {
                        eco_demands[indices[0]] * prices[indices[0] * 4]
                    }
                });

                if best.len() < top_n {
                    best.push(ResultRow {
                        score,
                        time: new_time,
                        routes,
                    });
                    best.sort_unstable_by(|a, b| {
                        a.score.partial_cmp(&b.score).unwrap_or(Ordering::Equal)
                    });
                } else if score > best[0].score {
                    best[0] = ResultRow {
                        score,
                        time: new_time,
                        routes,
                    };
                    best.sort_unstable_by(|a, b| {
                        a.score.partial_cmp(&b.score).unwrap_or(Ordering::Equal)
                    });
                }
                next_beam.push(BeamState {
                    time_used: new_time,
                    routes,
                    min_eco,
                    max_eco,
                    min_cargo,
                    max_cargo,
                });
            }
        }
        if next_beam.is_empty() {
            break;
        }
        if next_beam.len() > beam_width {
            next_beam.sort_unstable_by(|a, b| {
                score_cache[&b.routes]
                    .partial_cmp(&score_cache[&a.routes])
                    .unwrap_or(Ordering::Equal)
            });
            next_beam.truncate(beam_width);
        }
        beam = next_beam;
    }
    best.sort_unstable_by(|a, b| b.score.partial_cmp(&a.score).unwrap_or(Ordering::Equal));
    best
}

#[derive(Clone, Copy, Debug)]
struct Config {
    eco: i32,
    bus: i32,
    first: i32,
    cargo: i32,
}

struct Phase2<'a> {
    demands: &'a [f64],
    prices: &'a [f64],
    route_count: usize,
    max_pax: f64,
    max_ton: f64,
    max_waves: usize,
    tolerance: f64,
    wave_slack: f64,
    minimum: [f64; 4],
    max_first: i32,
    max_bus: i32,
    max_cargo: i32,
    first_step: i32,
    bus_step: i32,
    cargo_step: i32,
    best: Vec<Option<(f64, Config)>>,
    insertion_order: Vec<usize>,
}

impl<'a> Phase2<'a> {
    fn new(
        demands: &'a [f64],
        prices: &'a [f64],
        route_count: usize,
        max_pax: f64,
        max_ton: f64,
        max_waves: usize,
        overshoot_pct: f64,
        wave_slack: f64,
    ) -> Self {
        let mut minimum = [f64::INFINITY; 4];
        for route in 0..route_count {
            for class in 0..4 {
                minimum[class] = minimum[class].min(demands[route * 4 + class]);
            }
        }
        let max_first = ((max_ton / 0.15) as i32).min((max_pax / 4.2) as i32);
        let max_bus = ((max_ton / 0.125) as i32).min((max_pax / 1.8) as i32);
        let max_cargo = max_ton as i32;
        Self {
            demands,
            prices,
            route_count,
            max_pax,
            max_ton,
            max_waves,
            tolerance: 1.0 + overshoot_pct.max(0.0),
            wave_slack: wave_slack.max(0.0),
            minimum,
            max_first,
            max_bus,
            max_cargo,
            first_step: (max_first / 12).max(1),
            bus_step: (max_bus / 20).max(1),
            cargo_step: (max_cargo / 12).max(1),
            best: vec![None; max_waves + 1],
            insertion_order: Vec::new(),
        }
    }

    fn evaluate(&self, config: Config) -> (f64, usize) {
        let seats = [config.eco, config.bus, config.first, config.cargo];
        if seats.iter().all(|&value| value == 0) {
            return (0.0, 0);
        }
        let mut wave_limit = self.max_waves as f64;
        for route in 0..self.route_count {
            for class in 0..4 {
                let seat_count = seats[class];
                if seat_count <= 0 {
                    continue;
                }
                let demand = self.demands[route * 4 + class];
                if demand <= 0.0 {
                    return (0.0, 0);
                }
                let allowed = if class == 0 {
                    demand
                } else {
                    demand * self.tolerance
                };
                wave_limit = wave_limit.min(allowed / (2.0 * seat_count as f64));
            }
        }
        let waves = wave_limit.floor() as usize;
        if waves < 1 {
            return (0.0, 0);
        }

        let mut revenue = 0.0;
        for route in 0..self.route_count {
            for class in 0..4 {
                let seat_count = seats[class];
                if seat_count <= 0 {
                    continue;
                }
                let demand = self.demands[route * 4 + class];
                let price = self.prices[route * 4 + class];
                let capacity = 2.0 * seat_count as f64 * waves as f64;
                if price == 0.0 || demand == 0.0 || capacity <= 0.0 {
                    continue;
                }
                let sale_price = if capacity < demand {
                    (price * (1.0 - (capacity - demand) / (3.0 * demand))).floor()
                } else {
                    price
                };
                revenue += demand.min(capacity) * sale_price;
            }
        }
        (revenue, waves)
    }

    fn consider(&mut self, config: Config) {
        let (revenue, waves) = self.evaluate(config);
        if waves < 1 || revenue <= 0.0 {
            return;
        }
        if self.best[waves].is_none() {
            self.insertion_order.push(waves);
        }
        if self.best[waves]
            .as_ref()
            .is_none_or(|(current, _)| revenue > *current)
        {
            self.best[waves] = Some((revenue, config));
        }
    }

    fn eco_options(&self, eco_max: i32) -> Vec<i32> {
        let mut options = vec![eco_max];
        for waves in 1..=self.max_waves {
            let eco = (self.minimum[0] / (2 * waves) as f64) as i32;
            if eco < 1 {
                break;
            }
            options.push(eco.min(eco_max));
        }
        options.sort_unstable();
        options.dedup();
        options
    }

    fn pick(&self) -> Option<(Config, usize, f64)> {
        let mut top: Option<f64> = None;
        for &waves in &self.insertion_order {
            let revenue = self.best[waves].unwrap().0;
            if top.is_none_or(|current| revenue > current) {
                top = Some(revenue);
            }
        }
        let floor = top? * (1.0 - self.wave_slack);
        for waves in 1..self.best.len() {
            if let Some((revenue, config)) = self.best[waves] {
                if revenue >= floor {
                    return Some((config, waves, revenue));
                }
            }
        }
        None
    }

    fn revenue_leader(&self) -> Option<Config> {
        let mut leader = None;
        for &waves in &self.insertion_order {
            let (revenue, config) = self.best[waves].unwrap();
            if leader.is_none_or(|(current, _)| revenue > current) {
                leader = Some((revenue, config));
            }
        }
        leader.map(|(_, config)| config)
    }

    fn fine_tune(&mut self, center: Config) {
        let first_start = (center.first - self.first_step).max(0);
        let first_end = (center.first + self.first_step).min(self.max_first);
        let bus_start = (center.bus - self.bus_step).max(0);
        let bus_end = (center.bus + self.bus_step).min(self.max_bus);
        let cargo_start = (center.cargo - self.cargo_step).max(0);
        let cargo_end = (center.cargo + self.cargo_step).min(self.max_cargo);

        for first in first_start..=first_end {
            for bus in bus_start..=bus_end {
                for cargo in cargo_start..=cargo_end {
                    let payload = first as f64 * 0.15 + bus as f64 * 0.125 + cargo as f64 * 1.0;
                    if payload > self.max_ton {
                        break;
                    }
                    let seat_space = first as f64 * 4.2 + bus as f64 * 1.8;
                    if seat_space > self.max_pax {
                        break;
                    }
                    let eco_max = (((self.max_ton - payload) / 0.1) as i32)
                        .min(((self.max_pax - seat_space) / 1.0) as i32)
                        .max(0);
                    for eco in self.eco_options(eco_max) {
                        self.consider(Config {
                            eco,
                            bus,
                            first,
                            cargo,
                        });
                    }
                }
            }
        }
    }

    fn run(mut self) -> Option<(Config, usize, f64)> {
        let mut first = 0;
        while first <= self.max_first {
            let mut bus = 0;
            while bus <= self.max_bus {
                let mut cargo = 0;
                while cargo <= self.max_cargo {
                    let payload = first as f64 * 0.15 + bus as f64 * 0.125 + cargo as f64 * 1.0;
                    if payload > self.max_ton {
                        break;
                    }
                    let seat_space = first as f64 * 4.2 + bus as f64 * 1.8;
                    if seat_space > self.max_pax {
                        break;
                    }
                    let eco_max = (((self.max_ton - payload) / 0.1) as i32)
                        .min(((self.max_pax - seat_space) / 1.0) as i32)
                        .max(0);
                    for eco in self.eco_options(eco_max) {
                        self.consider(Config {
                            eco,
                            bus,
                            first,
                            cargo,
                        });
                    }
                    cargo += self.cargo_step;
                }
                bus += self.bus_step;
            }
            first += self.first_step;
        }

        for waves in 1..=self.max_waves {
            for mask in 0..8 {
                let first = if mask & 1 != 0 {
                    (self.minimum[2] * self.tolerance / (2 * waves) as f64) as i32
                } else {
                    0
                };
                let bus = if mask & 2 != 0 {
                    (self.minimum[1] * self.tolerance / (2 * waves) as f64) as i32
                } else {
                    0
                };
                let cargo = if mask & 4 != 0 {
                    (self.minimum[3] * self.tolerance / (2 * waves) as f64) as i32
                } else {
                    0
                };
                let payload = first as f64 * 0.15 + bus as f64 * 0.125 + cargo as f64 * 1.0;
                let seat_space = first as f64 * 4.2 + bus as f64 * 1.8;
                if payload > self.max_ton || seat_space > self.max_pax {
                    continue;
                }
                let eco_max = (((self.max_ton - payload) / 0.1) as i32)
                    .min(((self.max_pax - seat_space) / 1.0) as i32)
                    .max(0);
                let eco = ((self.minimum[0] / (2 * waves) as f64) as i32).min(eco_max);
                self.consider(Config {
                    eco,
                    bus,
                    first,
                    cargo,
                });
            }
        }

        if let Some(leader) = self.revenue_leader() {
            self.fine_tune(leader);
            if let Some((picked, _, _)) = self.pick() {
                self.fine_tune(picked);
            }
        }
        self.pick()
    }
}

/// Return the Phase 2 config as [eco, bus, first, cargo], wave count, and
/// daily revenue. The Python layer builds the small per-route breakdown.
#[no_mangle]
pub unsafe extern "C" fn optimize_circuit_native(
    demands: *const c_double,
    prices: *const c_double,
    route_count: c_longlong,
    max_pax: c_double,
    max_ton: c_double,
    max_waves: c_longlong,
    overshoot_pct: c_double,
    wave_slack: c_double,
    out_config: *mut c_int,
    out_waves: *mut c_longlong,
    out_revenue: *mut c_double,
) -> c_int {
    if route_count <= 0
        || max_waves < 0
        || demands.is_null()
        || prices.is_null()
        || out_config.is_null()
        || out_waves.is_null()
        || out_revenue.is_null()
    {
        return 0;
    }
    let route_count = route_count as usize;
    let demands = unsafe { std::slice::from_raw_parts(demands, route_count * 4) };
    let prices = unsafe { std::slice::from_raw_parts(prices, route_count * 4) };
    let result = Phase2::new(
        demands,
        prices,
        route_count,
        max_pax,
        max_ton,
        max_waves as usize,
        overshoot_pct,
        wave_slack,
    )
    .run();
    let Some((config, waves, revenue)) = result else {
        return 0;
    };
    unsafe {
        *out_config.add(0) = config.eco;
        *out_config.add(1) = config.bus;
        *out_config.add(2) = config.first;
        *out_config.add(3) = config.cargo;
        *out_waves = waves as c_longlong;
        *out_revenue = revenue;
    }
    1
}

/// Write the existing Phase 1 result buffers. Python validates every pointer
/// and dimension before crossing this boundary.
#[no_mangle]
pub unsafe extern "C" fn search_circuits_native(
    demands: *const c_double,
    prices: *const c_double,
    flight_times: *const c_double,
    eco_demands: *const c_double,
    cargo_demands: *const c_double,
    top_indices: *const c_longlong,
    n_top: c_longlong,
    max_pax: c_double,
    max_ton: c_double,
    max_waves: c_longlong,
    top_n: c_longlong,
    beam_width: c_longlong,
    max_steps: c_longlong,
    match_ratio: c_double,
    overshoot_pct: c_double,
    out_scores: *mut c_double,
    out_times: *mut c_double,
    out_counts: *mut c_int,
    out_indices: *mut c_int,
) -> c_int {
    if n_top < 0
        || n_top as usize > MAX_ROUTES
        || top_n <= 0
        || max_waves < 0
        || beam_width < 0
        || max_steps < 0
        || demands.is_null()
        || prices.is_null()
        || flight_times.is_null()
        || eco_demands.is_null()
        || cargo_demands.is_null()
        || top_indices.is_null()
        || out_scores.is_null()
        || out_times.is_null()
        || out_counts.is_null()
        || out_indices.is_null()
    {
        return 0;
    }
    let route_count = n_top as usize;
    let demands = unsafe { std::slice::from_raw_parts(demands, route_count * 4) };
    let prices = unsafe { std::slice::from_raw_parts(prices, route_count * 4) };
    let flight_times = unsafe { std::slice::from_raw_parts(flight_times, route_count) };
    let eco_demands = unsafe { std::slice::from_raw_parts(eco_demands, route_count) };
    let cargo_demands = unsafe { std::slice::from_raw_parts(cargo_demands, route_count) };
    let top_indices = unsafe { std::slice::from_raw_parts(top_indices, route_count) };
    if top_indices
        .iter()
        .any(|&index| index < 0 || index as usize >= route_count)
    {
        return 0;
    }

    let results = beam_search(
        demands,
        prices,
        flight_times,
        eco_demands,
        cargo_demands,
        top_indices,
        max_pax,
        max_ton,
        max_waves as usize,
        top_n as usize,
        beam_width as usize,
        max_steps as usize,
        match_ratio,
        overshoot_pct,
    );
    for (slot, result) in results.iter().enumerate() {
        unsafe {
            *out_scores.add(slot) = result.score;
            *out_times.add(slot) = result.time;
        }
        let indices = result.routes.indices();
        unsafe {
            *out_counts.add(slot) = indices.len() as c_int;
            for (column, index) in indices.into_iter().enumerate() {
                *out_indices.add(slot * MAX_ROUTES + column) = index as c_int;
            }
        }
    }
    results.len() as c_int
}
