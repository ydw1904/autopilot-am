"""ctypes wrapper around the C++ beam-search dylib (code/native/).

Drop-in replacement for the old Rust/pyo3 ``circuit_planner_native``
extension: same module name, same ``search_circuits_native`` signature,
same return shape ``[(score, total_time, [route_idx, ...]), ...]``.

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
    _c_double_p, _c_double_p, _c_int32_p, _c_int32_p,
]

_MAX_ROUTES = 192  # bitset capacity in beam_search.cpp


def _dptr(arr):
    return arr.ctypes.data_as(_c_double_p)


def search_circuits_native(demands, prices, flight_times, eco_demands,
                           cargo_demands, top_indices, max_pax, max_ton,
                           max_waves, top_n, beam_width, max_steps,
                           match_ratio):
    demands = np.ascontiguousarray(demands, dtype=np.float64)
    prices = np.ascontiguousarray(prices, dtype=np.float64)
    flight_times = np.ascontiguousarray(flight_times, dtype=np.float64)
    eco_demands = np.ascontiguousarray(eco_demands, dtype=np.float64)
    cargo_demands = np.ascontiguousarray(cargo_demands, dtype=np.float64)
    top_indices = np.ascontiguousarray(top_indices, dtype=np.int64)

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
