#!/bin/sh
# Build the native planner dylib. Needs only rustc; there are no crates to fetch.
set -e
cd "$(dirname "$0")"
case "$(uname)" in
    Darwin) EXT=dylib ;;
    *)      EXT=so ;;
esac
# Ordinary Rust floating-point expressions do not contract into fused
# multiply-add operations, which keeps floor-sensitive scores reproducible.
rustc --edition 2021 -C opt-level=3 \
  --crate-type cdylib -o "libbeamsearch.$EXT" beam_search.rs
echo "built libbeamsearch.$EXT"
