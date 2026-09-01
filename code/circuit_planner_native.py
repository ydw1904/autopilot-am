"""ctypes wrapper around the Rust planner dylib (code/native/).

The plain C ABI keeps the same ``search_circuits_native`` signature and
return shape ``[(score, total_time, [route_idx, ...]), ...]``.

Build the dylib with code/native/build.sh; if it is missing, importing
this module raises ImportError and circuit_planner.py falls back to the
pure-Python search.
"""

import ctypes
import os
import sys

import numpy as np

_EXT = "dylib" if sys.platform == "darwin" else "so"
_LIB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         "native", f"libbeamsearch.{_EXT}")

if not os.path.exists(_LIB_PATH):
    raise ImportError(
        f"{_LIB_PATH} not found — build it with code/native/build.sh")

_lib = ctypes.CDLL(_LIB_PATH)

_c_double_p = ctypes.POINTER(ctypes.c_double)
_c_int64_p = ctypes.POINTER(ctypes.c_int64)
_c_int32_p = ctypes.POINTER(ctypes.c_int32)

_lib.search_circuits_native.restype = ctypes.c_int
_lib.search_circuits_native.argtypes = [
    _c_double_p, _c_double_p, _c_double_p, _c_double_p, _c_double_p,
    _c_int64_p, ctypes.c_int64,
    ctypes.c_double, ctypes.c_double, ctypes.c_int64,
    ctypes.c_int64, ctypes.c_int64, ctypes.c_int64, ctypes.c_double,
    ctypes.c_double,
    _c_double_p, _c_double_p, _c_int32_p, _c_int32_p,
]

try:
    _optimize_circuit = _lib.optimize_circuit_native
except AttributeError as exc:
    raise ImportError(
        f"{_LIB_PATH} is stale; rebuild it with code/native/build.sh") from exc
_optimize_circuit.restype = ctypes.c_int
_optimize_circuit.argtypes = [
    _c_double_p, _c_double_p, ctypes.c_int64,
    ctypes.c_double, ctypes.c_double, ctypes.c_int64,
    ctypes.c_double, ctypes.c_double,
    _c_int32_p, _c_int64_p, _c_double_p,
]

_MAX_ROUTES = 192  # bitset capacity in beam_search.rs


def _dptr(arr):
    return arr.ctypes.data_as(_c_double_p)


def search_circuits_native(demands, prices, flight_times, eco_demands,
                           cargo_demands, top_indices, max_pax, max_ton,
                           max_waves, top_n, beam_width, max_steps,
                           match_ratio, overshoot_pct=0.0):
    demands = np.ascontiguousarray(demands, dtype=np.float64)
    prices = np.ascontiguousarray(prices, dtype=np.float64)
    flight_times = np.ascontiguousarray(flight_times, dtype=np.float64)
    eco_demands = np.ascontiguousarray(eco_demands, dtype=np.float64)
    cargo_demands = np.ascontiguousarray(cargo_demands, dtype=np.float64)
    top_indices = np.ascontiguousarray(top_indices, dtype=np.int64)

    if len(top_indices) > _MAX_ROUTES:
        raise ValueError(
            f"top_indices has {len(top_indices)} entries; the native RouteSet "
            f"bitset holds at most {_MAX_ROUTES}")
    if top_n <= 0:
        raise ValueError("top_n must be positive")
    m = len(top_indices)
    if np.any(top_indices < 0) or np.any(top_indices >= m):
        raise ValueError(f"top_indices entries must be between 0 and {m - 1}")
    for name, arr in (("demands", demands), ("prices", prices)):
        if arr.shape != (m, 4):
            raise ValueError(f"{name} must have shape ({m}, 4), got {arr.shape}")
    for name, arr in (("flight_times", flight_times),
                      ("eco_demands", eco_demands),
                      ("cargo_demands", cargo_demands)):
        if arr.shape != (m,):
            raise ValueError(f"{name} must have shape ({m},), got {arr.shape}")

    out_scores = np.empty(top_n, dtype=np.float64)
    out_times = np.empty(top_n, dtype=np.float64)
    out_counts = np.empty(top_n, dtype=np.int32)
    out_indices = np.empty(top_n * _MAX_ROUTES, dtype=np.int32)

    n = _lib.search_circuits_native(
        _dptr(demands), _dptr(prices), _dptr(flight_times),
        _dptr(eco_demands), _dptr(cargo_demands),
        top_indices.ctypes.data_as(_c_int64_p), len(top_indices),
        float(max_pax), float(max_ton), int(max_waves),
        int(top_n), int(beam_width), int(max_steps), float(match_ratio),
        float(overshoot_pct),
        _dptr(out_scores), _dptr(out_times),
        out_counts.ctypes.data_as(_c_int32_p),
        out_indices.ctypes.data_as(_c_int32_p),
    )

    return [
        (float(out_scores[i]), float(out_times[i]),
         [int(x) for x in out_indices[i * _MAX_ROUTES:
                                      i * _MAX_ROUTES + out_counts[i]]])
        for i in range(n)
    ]


def optimize_circuit_native(demands, prices, max_pax, max_ton, max_waves,
                            overshoot_pct=0.0, wave_slack=0.02):
    demands = np.ascontiguousarray(demands, dtype=np.float64)
    prices = np.ascontiguousarray(prices, dtype=np.float64)
    if demands.ndim != 2 or demands.shape[1] != 4 or demands.shape[0] == 0:
        raise ValueError(f"demands must have shape (N, 4), got {demands.shape}")
    if prices.shape != demands.shape:
        raise ValueError(f"prices must have shape {demands.shape}, got {prices.shape}")

    out_config = np.empty(4, dtype=np.int32)
    out_waves = np.empty(1, dtype=np.int64)
    out_revenue = np.empty(1, dtype=np.float64)
    found = _optimize_circuit(
        _dptr(demands), _dptr(prices), len(demands),
        float(max_pax), float(max_ton), int(max_waves),
        float(overshoot_pct), float(wave_slack),
        out_config.ctypes.data_as(_c_int32_p),
        out_waves.ctypes.data_as(_c_int64_p), _dptr(out_revenue),
    )
    if not found:
        return None, 1, -1
    revenue = float(out_revenue[0])
    if revenue.is_integer():
        revenue = int(revenue)
    return ({"eco": int(out_config[0]), "bus": int(out_config[1]),
             "fir": int(out_config[2]), "cargo": int(out_config[3])},
            int(out_waves[0]), revenue)
