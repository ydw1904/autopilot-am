# Verification and common tasks


Pure logic is covered by pytest; everything that touches CDP or the live game is
verified by hand.

```bash
# Test suite: offline, no Chrome, ~3s. 197 passed, 1 xfailed as of 2026-08-23.
.venv/bin/python -m pytest code/tests/ -q

# Browser app production build
cd web && bun run build

# Planner smoke test (no game connection needed)
python3 code/circuit_planner.py --hub HKG --aircraft B742 --circuits 2 --phase1-only
python3 code/circuit_planner.py --hub HKG --aircraft B742 --circuits 2   # full Phase 1+2

# MCP server boots and registers all tools
.venv/bin/python -c "import asyncio,sys; sys.path.insert(0,'code'); import mcp_server; \
  print(len(asyncio.run(mcp_server.mcp.list_tools())), 'tools')"   # -> 45 tools

# Mobile API surface (needs a valid ~/.airlines_manager/session.json)
.venv/bin/python -c "import sys; sys.path.insert(0,'code'); import mcp_server; \
  print(mcp_server.mobile_balance())"   # a dollar balance means the mobile token is live

# Live stack (Chrome up + logged in): a dollar balance means CDP + session work
.venv/bin/python -c "import sys; sys.path.insert(0,'code'); import mcp_server; \
  print(mcp_server.get_balance())"

# Livery sync (mobile passes need only the session; --web needs Chrome up)
.venv/bin/python code/skin_name_sync.py --dry-run

# DB sanity
sqlite3 db/am_aircraft.db "SELECT COUNT(*) FROM routes WHERE hub_iata='HKG' AND eco_demand>0"

# Livery coverage: every owned livery named, and classed by where it comes from
sqlite3 db/am_aircraft.db "SELECT s.source, COUNT(f.aircraft_id) FROM fleet f \
  JOIN mobile_skins s ON s.skin_id=f.skin_id GROUP BY 1"
```

Rebuild the native optimizer after editing `native/beam_search.rs`:
`bash code/native/build.sh` (the planner falls back to pure Python if the dylib is
absent).

## Common Tasks

- **Add an aircraft:** confirm it's in `aircraft`, add to `ALIASES` in
  `circuit_planner.py` (and `aircraft_buyer.py` if purchasing).
- **Add a hub:** confirm routes exist (`SELECT COUNT(*) … WHERE hub_iata='XXX'`), run
  the planner with `--phase1-only`; for buying, get the numeric hub id from the game URL
  (differs from the IATA code).
- **Change pricing:** edit the `ideal_*` / `supersim_price` / `daily_turnover` helpers
  in `circuit_planner.py`. Remember bus/fir are eco multipliers, not independent.
- **Add an MCP tool:** wrap the corresponding CLI/core function, give it a typed
  signature, default any mutation to `dry_run=True`, return a `dict`.

