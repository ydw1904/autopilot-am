"""Exact-output characterization tests for the native planner boundary."""

import hashlib
import json
import struct
from copy import deepcopy

import numpy as np
import pytest

native = pytest.importorskip("circuit_planner_native")


def _phase1_cases():
    for case, size in enumerate((1, 9, 70, 150, 192)):
        i = np.arange(size, dtype=np.float64)
        demands = np.column_stack((
            3500 + (i * 137 + case * 17) % 4200,
            180 + (i * 31 + case * 7) % 500,
            20 + (i * 11 + case * 3) % 95,
            280 + (i * 47 + case * 13) % 700,
        ))
        prices = np.column_stack((
            700 + (i * 53) % 4100,
            950 + (i * 71) % 5200,
            1600 + (i * 97) % 8900,
            1200 + (i * 109) % 13000,
        ))
        flight_times = 2.0 + ((i * 7 + case) % 121) * 0.25
        top = np.arange(size, dtype=np.int64)
        if case % 2:
            top = top[::-1].copy()
        yield (
            demands, prices, flight_times, demands[:, 0].copy(),
            demands[:, 3].copy(), top, 730.0 - case * 37,
            76.0 - case * 4.5, 30, 5, 96, 10,
            0.72 + case * 0.04, 0.1 if case % 2 else 0.0,
        )


def _phase1_bytes(results):
    out = bytearray(struct.pack("<I", len(results)))
    for score, total_time, indices in results:
        out.extend(struct.pack("<ddI", score, total_time, len(indices)))
        out.extend(struct.pack(f"<{len(indices)}i", *indices))
    return bytes(out)


def test_phase1_native_results_are_byte_identical_to_cpp_oracle():
    output = b"".join(
        _phase1_bytes(native.search_circuits_native(*case))
        for case in _phase1_cases()
    )
    assert len(output) == 1080
    assert hashlib.sha256(output).hexdigest() == (
        "57ea4b42493822031364b82389b8d24416e7e15d69e887ed4be6401a0d5b6ea2"
    )


def _routes(count, seed):
    return [{
        "iata": f"R{i:02d}",
        "dist": 1800 + ((i * 1171 + seed * 431) % 11800),
        "eco_d": 4100 + ((i * 389 + seed * 97) % 3700),
        "bus_d": 180 + ((i * 53 + seed * 17) % 430),
        "fir_d": 18 + ((i * 19 + seed * 5) % 90),
        "cargo_d": 260 + ((i * 73 + seed * 31) % 650),
    } for i in range(count)]


@pytest.mark.parametrize("routes,aircraft,comfort,speed,max_waves,overshoot,wave_slack,expected", [
    (_routes(3, 1), {"pax": 48, "tonnage": 8.0}, 500, 700, 12, 0.0, 0.02,
     ({"eco": 48, "bus": 0, "fir": 0, "cargo": 3}, 12, 6791544)),
    (_routes(7, 2), {"pax": 730, "tonnage": 76.0}, 500, 700, 30, 0.0, 0.02,
     ({"eco": 429, "bus": 21, "fir": 2, "cargo": 30}, 5, 96882130)),
    (_routes(6, 3), {"pax": 595, "tonnage": 68.0}, 650, 850, 24, 0.1, 0.0,
     ({"eco": 274, "bus": 15, "fir": 1, "cargo": 24}, 8, 90160438)),
])
def test_phase2_native_config_matches_python_oracle(
        routes, aircraft, comfort, speed, max_waves, overshoot, wave_slack,
        expected):
    from circuit_planner import ideal_bus, ideal_cargo, ideal_eco, ideal_fir

    demands = np.array([
        [r["eco_d"], r["bus_d"], r["fir_d"], r["cargo_d"]]
        for r in routes
    ], dtype=np.float64)
    prices = np.array([
        [ideal_eco(r["dist"], comfort), ideal_bus(r["dist"], comfort),
         ideal_fir(r["dist"], comfort), ideal_cargo(r["dist"], speed)]
        for r in routes
    ], dtype=np.float64)

    assert native.optimize_circuit_native(
        demands, prices, aircraft["pax"], aircraft["tonnage"], max_waves,
        overshoot, wave_slack,
    ) == expected


def test_phase2_results_are_byte_identical_to_python_oracle():
    from circuit_planner import optimize_circuit

    cases = [
        (_routes(3, 1), {"pax": 48, "tonnage": 8.0}, 500, 700, 12, 0.0, 0.02),
        (_routes(7, 2), {"pax": 730, "tonnage": 76.0}, 500, 700, 30, 0.0, 0.02),
        (_routes(6, 3), {"pax": 595, "tonnage": 68.0}, 650, 850, 24, 0.1, 0.0),
    ]
    output = b"".join(
        json.dumps(
            optimize_circuit(
                deepcopy(routes), aircraft, comfort=comfort, speed=speed,
                max_waves=max_waves, overshoot_pct=overshoot,
                wave_slack=wave_slack,
            ),
            sort_keys=True, separators=(",", ":"), ensure_ascii=True,
        ).encode()
        for routes, aircraft, comfort, speed, max_waves, overshoot, wave_slack
        in cases
    )
    assert len(output) == 8495
    assert hashlib.sha256(output).hexdigest() == (
        "4359bc9be9d6ba192a7cf239f1f9573be2196219647176626258d0a5a2ee88aa"
    )
