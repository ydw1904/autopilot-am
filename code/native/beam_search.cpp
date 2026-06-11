// Circuit-planner beam search, C++ port of the former Rust (pyo3) module.
// Built as a plain C-ABI dylib and loaded from Python via ctypes
// (see code/circuit_planner_native.py). Build: ./build.sh (one clang++ call).

#include <algorithm>
#include <cmath>
#include <cstdint>
#include <cstring>
#include <unordered_map>
#include <unordered_set>
#include <vector>

namespace {

// ─── RouteSet: bitset for up to 192 routes ────────────────────────────

struct RouteSet {
    uint64_t w[3];

    bool operator==(const RouteSet& o) const {
        return w[0] == o.w[0] && w[1] == o.w[1] && w[2] == o.w[2];
    }

    bool contains(size_t i) const { return w[i / 64] & (1ULL << (i % 64)); }

    RouteSet with(size_t i) const {
        RouteSet s = *this;
        s.w[i / 64] |= 1ULL << (i % 64);
        return s;
    }

    std::vector<size_t> to_indices() const {
        std::vector<size_t> v;
        for (size_t word = 0; word < 3; word++) {
            uint64_t bits = w[word];
            while (bits) {
                v.push_back(word * 64 + __builtin_ctzll(bits));
                bits &= bits - 1;
            }
        }
        return v;
    }
};

struct RouteSetHash {
    size_t operator()(const RouteSet& s) const {
        // FxHash-style mix, same spirit as the rustc-hash used before.
        uint64_t h = 0;
        for (uint64_t word : s.w) {
            h = (h ^ word) * 0x517cc1b727220a95ULL;
            h = (h << 26) | (h >> 38);
        }
        return static_cast<size_t>(h);
    }
};

// ─── Scoring (port of _quick_revenue_estimate_nb) ─────────────────────

double eval_config(int eco, int bus, int fir, int cargo,
                   const std::vector<size_t>& indices,
                   const double* demands, const double* prices,
                   double max_pax, double max_ton, size_t max_waves) {
    if (eco + bus + fir + cargo == 0) return 0.0;
    double eco_f = eco, bus_f = bus, fir_f = fir, cargo_f = cargo;

    if (eco_f * 0.1 + bus_f * 0.125 + fir_f * 0.15 + cargo_f > max_ton) return 0.0;
    if (eco_f + bus_f * 1.8 + fir_f * 4.2 > max_pax) return 0.0;

    int seat_arr[4] = {eco, bus, fir, cargo};

    // Size waves to ECO demand only. Eco is the highest-volume class with the
    // best revenue/seat-cost ratio in this game. Sizing waves to satisfy all
    // classes pushes wave counts very high for marginal FIR/BUS gain — bad ROI.
    // Bus/fir/cargo demand is filled as a by-product of the same aircraft.
    double mw_f = -1.0;
    if (eco > 0) {
        for (size_t idx : indices) {
            double d = demands[idx * 4];  // c=0 is eco
            if (d <= 0.0) return 0.0;
            double wl = d / (2.0 * eco_f);
            if (wl > mw_f) mw_f = wl;
        }
    } else {
        // No eco seats: fall back to whichever non-eco class exists.
        for (size_t idx : indices) {
            size_t base = idx * 4;
            for (int c = 1; c < 4; c++) {
                int s = seat_arr[c];
                if (s <= 0) continue;
                double d = demands[base + c];
                if (d <= 0.0) return 0.0;
                double wl = d / (2.0 * s);
                if (wl > mw_f) mw_f = wl;
            }
        }
    }

    if (mw_f < 0.0) return 0.0;
    size_t mw = static_cast<size_t>(std::ceil(mw_f));
    if (mw > max_waves) mw = max_waves;
    if (mw < 1) return 0.0;

    double rev = 0.0;
    for (size_t idx : indices) {
        size_t base = idx * 4;
        for (int c = 0; c < 4; c++) {
            int s = seat_arr[c];
            if (s <= 0) continue;
            double d = demands[base + c];
            double p = prices[base + c];
            double cap = 2.0 * s * static_cast<double>(mw);
            if (p == 0.0 || d == 0.0) continue;
            double ss = cap < d ? std::floor(p * (1.0 - (cap - d) / (3.0 * d))) : p;
            double filled = d < cap ? d : cap;
            rev += filled * ss;
        }
    }
    return rev;
}

double quick_score(const std::vector<size_t>& indices,
                   const double* demands, const double* prices,
                   double max_pax, double max_ton, size_t max_waves) {
    if (indices.empty()) return 0.0;

    double min_d[4] = {HUGE_VAL, HUGE_VAL, HUGE_VAL, HUGE_VAL};
    for (size_t idx : indices) {
        size_t base = idx * 4;
        for (int c = 0; c < 4; c++) {
            double v = demands[base + c];
            if (v < min_d[c]) min_d[c] = v;
        }
    }

    double best = 0.0;
    static const double tw_arr[6] = {3.0, 5.0, 8.0, 10.0, 15.0, 20.0};

    for (double tw : tw_arr) {
        for (int fb = 0; fb < 4; fb++) {
            bool fir_on = (fb >> 1) & 1;
            bool bus_on = fb & 1;

            int fir_s = fir_on ? static_cast<int>(min_d[2] / (2.0 * tw)) : 0;
            fir_s = std::max(fir_s, 0);
            int bus_s = bus_on ? static_cast<int>(min_d[1] / (2.0 * tw)) : 0;
            bus_s = std::max(bus_s, 0);
            int cargo_s = std::max(static_cast<int>(min_d[3] / (2.0 * tw)), 0);

            int eco_dem = std::max(static_cast<int>(min_d[0] / (2.0 * tw)), 0);
            int eco_pay = std::max(static_cast<int>(
                (max_ton - bus_s * 0.125 - fir_s * 0.15 - cargo_s) / 0.1), 0);
            int eco_seat = std::max(static_cast<int>(
                max_pax - bus_s * 1.8 - fir_s * 4.2), 0);
            int eco_s = std::min({eco_dem, eco_pay, eco_seat});

            double r = eval_config(eco_s, bus_s, fir_s, cargo_s, indices,
                                   demands, prices, max_pax, max_ton, max_waves);
            if (r > best) best = r;
        }
    }
    return best;
}

// ─── Beam Search ──────────────────────────────────────────────────────

struct BeamState {
    double time_used;
    RouteSet routes;
    double mn_eco, mx_eco, mn_cgo, mx_cgo;
};

struct Result {
    double score;
    double time;
    RouteSet routes;
};

std::vector<Result> beam_search(
    const double* demands, const double* prices, const double* flight_times,
    const double* eco_demands, const double* cargo_demands,
    const int64_t* top_indices, size_t n_top,
    double max_pax, double max_ton, size_t max_waves,
    size_t top_n, size_t beam_width, size_t max_steps, double match_ratio) {

    std::vector<BeamState> beam = {
        {0.0, {{0, 0, 0}}, HUGE_VAL, 0.0, HUGE_VAL, 0.0}};

    std::vector<Result> best;
    best.reserve(top_n + 1);
    std::unordered_set<RouteSet, RouteSetHash> seen;
    std::unordered_map<RouteSet, double, RouteSetHash> score_cache;

    auto by_score_asc = [](const Result& a, const Result& b) {
        return a.score < b.score;
    };

    for (size_t step = 0; step < max_steps; step++) {
        std::vector<BeamState> next_beam;

        for (const BeamState& state : beam) {
            for (size_t k = 0; k < n_top; k++) {
                size_t ri = static_cast<size_t>(top_indices[k]);
                if (state.routes.contains(ri)) continue;

                double new_time = state.time_used + flight_times[ri];
                if (new_time > 168.0) continue;

                double new_mn_eco = std::min(state.mn_eco, eco_demands[ri]);
                double new_mx_eco = std::max(state.mx_eco, eco_demands[ri]);
                double new_mn_cgo = std::min(state.mn_cgo, cargo_demands[ri]);
                double new_mx_cgo = std::max(state.mx_cgo, cargo_demands[ri]);

                if (new_mx_eco > 0.0 && new_mn_eco / new_mx_eco < match_ratio) continue;
                if (new_mx_cgo > 0.0 && new_mn_cgo / new_mx_cgo < match_ratio) continue;

                RouteSet new_routes = state.routes.with(ri);
                if (!seen.insert(new_routes).second) continue;

                auto [it, inserted] = score_cache.try_emplace(new_routes, 0.0);
                if (inserted) {
                    std::vector<size_t> indices = new_routes.to_indices();
                    if (indices.size() >= 2) {
                        it->second = quick_score(indices, demands, prices,
                                                 max_pax, max_ton, max_waves);
                    } else if (indices.size() == 1) {
                        it->second = eco_demands[indices[0]] * prices[indices[0] * 4];
                    }
                }
                double score = it->second;

                if (best.size() < top_n) {
                    best.push_back({score, new_time, new_routes});
                    std::sort(best.begin(), best.end(), by_score_asc);
                } else if (score > best[0].score) {
                    best[0] = {score, new_time, new_routes};
                    std::sort(best.begin(), best.end(), by_score_asc);
                }

                next_beam.push_back({new_time, new_routes, new_mn_eco,
                                     new_mx_eco, new_mn_cgo, new_mx_cgo});
            }
        }

        if (next_beam.empty()) break;

        if (next_beam.size() > beam_width) {
            std::sort(next_beam.begin(), next_beam.end(),
                      [&](const BeamState& a, const BeamState& b) {
                          auto ia = score_cache.find(a.routes);
                          auto ib = score_cache.find(b.routes);
                          double sa = ia != score_cache.end() ? ia->second : 0.0;
                          double sb = ib != score_cache.end() ? ib->second : 0.0;
                          return sb < sa;
                      });
            next_beam.resize(beam_width);
        }

        beam = std::move(next_beam);
    }

    std::sort(best.begin(), best.end(),
              [](const Result& a, const Result& b) { return b.score < a.score; });
    return best;
}

}  // namespace

// ─── C ABI (consumed by code/circuit_planner_native.py via ctypes) ────
//
// Output is written into caller-allocated flat buffers, top_n slots each:
//   out_scores[top_n], out_times[top_n], out_counts[top_n],
//   out_indices[top_n * 192] (row i holds out_counts[i] route indices).
// Returns the number of result slots filled.

extern "C" int search_circuits_native(
    const double* demands, const double* prices, const double* flight_times,
    const double* eco_demands, const double* cargo_demands,
    const int64_t* top_indices, int64_t n_top,
    double max_pax, double max_ton, int64_t max_waves,
    int64_t top_n, int64_t beam_width, int64_t max_steps, double match_ratio,
    double* out_scores, double* out_times, int32_t* out_counts,
    int32_t* out_indices) {

    std::vector<Result> results = beam_search(
        demands, prices, flight_times, eco_demands, cargo_demands,
        top_indices, static_cast<size_t>(n_top), max_pax, max_ton,
        static_cast<size_t>(max_waves), static_cast<size_t>(top_n),
        static_cast<size_t>(beam_width), static_cast<size_t>(max_steps),
        match_ratio);

    int n = 0;
    for (const Result& r : results) {
        if (n >= top_n) break;
        out_scores[n] = r.score;
        out_times[n] = r.time;
        std::vector<size_t> idx = r.routes.to_indices();
        out_counts[n] = static_cast<int32_t>(idx.size());
        for (size_t j = 0; j < idx.size(); j++) {
            out_indices[n * 192 + j] = static_cast<int32_t>(idx[j]);
        }
        n++;
    }
    return n;
}
