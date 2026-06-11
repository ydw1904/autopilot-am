#!/bin/sh
# Build the beam-search dylib. Needs only the system C++ compiler
# (Apple clang on macOS, g++/clang++ on Linux). No Rust, no pip packages.
set -e
cd "$(dirname "$0")"
case "$(uname)" in
    Darwin) EXT=dylib ;;
    *)      EXT=so ;;
esac
# -ffp-contract=off: no fused multiply-add, keeps float results identical
# to the old Rust build (scores go through floor(), so rounding matters).
c++ -O3 -std=c++17 -ffp-contract=off -shared -fPIC -o "libbeamsearch.$EXT" beam_search.cpp
echo "built libbeamsearch.$EXT"
