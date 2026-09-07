# AGENTS.md: Guide for AI Agents Working on This Codebase

**This is the single source of truth for every AI coding agent working in this repo**
(Claude Code, OpenCode, Codex, Cursor, Gemini CLI, or anything else). `CLAUDE.md`
and `GEMINI.md` are one-line pointers back here. Learn something durable? Edit
*this* file if it is a rule, the matching file in `docs/` if it is detail.

## Rules that bite (read these first)

1. **Mutating MCP tools default `dry_run=True`** and return a structured `dict`.
   Keep that contract when adding tools.
2. **Never hardcode paths.** Use `db.py`'s `DB` constant and the
   `os.path.dirname(os.path.abspath(__file__))` pattern.
3. **Two shared layers, no copies.** All CDP work goes through `code/cdp.py`, all
   DB work through `code/db.py`. Never add a second CDP client or DB-path definition.
4. **Mobile API is primary, CDP is the fallback.** The mobile JSON API reports
   refusals as errors; the web forms fail silently. Still CDP-only: buying routes
   and aircraft, the demand refresh, `raw-ideal` pricing, `route_details.py`.
   See [`tickets/013-mobile-api-remaining-gaps.md`](tickets/013-mobile-api-remaining-gaps.md)
   for what has already been tried on the remaining gaps.
5. **Route purchase uses the country-listing `form.submit()` flow, never `fetch()`.**
   The game answers `200 OK` to a `fetch()` purchase POST without buying. Do not
   "simplify" `circuit_route_buyer.py` back to it.
6. **The web page's "ideal price" is wrong for business/first.** Always derive:
   `bus = floor(eco × 1.33)`, `fir = floor(eco × 2.3)`. The *mobile* audit price is
   already corrected, so aim at `audit.price` directly there.
7. **A mobile rename is a reconfigure that echoes the current seats back verbatim.**
   Re-read the seats from the profile per call; wrong seats charge money and
   reconfigure the plane.
8. **Never send `apply_to_all="1"` to `shop/skin/apply`.** It repaints the whole
   fleet, and an awarded challenge/event livery cannot be re-applied once painted over.
9. **`sell_for_scrap()` is the one unverified write.** One id, no batch, no
   apply-to-all. Do not give it a cascade.
10. **All mobile traffic is paced** (`mobile_api.PACER`, process-wide). Don't
    parallelize sweeps to go faster; conspicuous traffic is the failure mode.
11. **No native `<select>` in `web/src/`.** Every dropdown is `<MenuSelect>`; there
    are no `<select>` elements left, so a new one is always a mistake.
12. **Never break `.venv/bin/python -m pytest code/tests/ -q`.** Offline, ~3s, and
    it is the only automatic check this repo has.

## What this is

Agent control plane for the browser game
[Airlines Manager](https://www.airlines-manager.com): an **MCP server exposing 45
tools** over a live, logged-in game session, plus the optimization engine and
browser-automation layer it drives. Goal: maximize weekly revenue by selecting
circuits (route sets), seat configs, schedules, and prices. For what the project
*is* and how to drive it, see `README.md`.

**Language:** Python 3.10+, deps in `code/requirements.txt`. Browser automation via
Chrome DevTools Protocol. Both circuit optimization phases run in native Rust
(`code/native/beam_search.rs`) behind a ctypes wrapper, with Python fallbacks. The
UI is React/Vite served by FastAPI.

Three surfaces on top of `cdp.py` + `db.py`:

1. **MCP server** (`code/mcp_server.py`): 45 typed tools, the agent-facing plane.
2. **CLI scripts** (`code/*.py`): one `argparse` script per game operation; the
   heavy ones take `--json` (one JSON document on stdout, human report on stderr).
3. **Browser app** (`code/api_server.py` + `web/src/`): `run_web.sh` for
   production, `run_dev.sh` for Vite hot reload on :3000 + FastAPI on :8000.

Generated state (`db/*.db`, `data/`) is **not** committed.

## Deep documentation

| File | Covers |
|------|--------|
| [`docs/architecture.md`](docs/architecture.md) | project overview, the three surfaces, directory layout, key game concepts (circuits, waves, SuperSim pricing, demand constraint), the SQLite data model |
| [`docs/modules.md`](docs/modules.md) | `circuit_planner`, the native optimizer, the route/aircraft buyers, `route_details`, `auto_pricer`, `circuit_scheduler`, the fleet/data-sync scripts, `alliance_donator` |
| [`docs/mobile-api.md`](docs/mobile-api.md) | the mobile JSON API: sessions and token renewal, every verified endpoint, schedule writes, SHM economics, the APK il2cpp metadata trick, request pacing, which web tools now run mobile-first |
| [`docs/shm-and-syncs.md`](docs/shm-and-syncs.md) | `shm_watcher` standing orders and guards, `daily_routine`, the mobile twins (`mobile_pricer` / `mobile_renamer` / `mobile_reconfigurator`), `booster_sync`, `skin_name_sync`, `purchase_date_sync`, `mobile_login`, the mitmproxy capture pipeline |
| [`docs/web-ui.md`](docs/web-ui.md) | `MenuSelect`, `TagPicker`, the read-only Network / Circuits / Pricing / Ops workspaces, the pricing apply flow, the aircraft editor |
| [`docs/verification.md`](docs/verification.md) | the full verification command set and common tasks (add an aircraft, add a hub, change pricing, add an MCP tool) |
| [`tickets/`](tickets/) | open work, each with what has already been tried |

## Conventions

- **CLI:** every script uses `argparse` and a `main()`; mutating scripts expose
  `--dry-run`.
- **DB access:** direct `sqlite3`, no ORM. Parameterize queries.
- **Aircraft aliases** live in the `ALIASES` dict in `circuit_planner.py`, mirrored
  in `aircraft_buyer.py`.
- **Sort menus:** name ascending first and the default, name descending second, then
  each remaining field as a "most first" / "least first" pair, direction spelled out
  in `hint`. Only offer a sort the data can deliver (`FLEET_SORTS` in `db.py`).
- **The Network / Circuits / Pricing / Ops tabs are read-only on purpose.** The
  writes they front have preconditions the CLI scripts already encode; duplicating
  them in the UI is how the two drift apart.

## Verification

```bash
# Test suite: offline, no Chrome, ~3s. 197 passed, 1 xfailed as of 2026-08-23.
.venv/bin/python -m pytest code/tests/ -q

# Browser app production build
cd web && bun run build

# MCP server boots and registers all tools -> 45 tools
.venv/bin/python -c "import asyncio,sys; sys.path.insert(0,'code'); import mcp_server; \
  print(len(asyncio.run(mcp_server.mcp.list_tools())), 'tools')"

# Mobile session live (needs ~/.airlines_manager/session.json); a dollar balance means yes
.venv/bin/python -c "import sys; sys.path.insert(0,'code'); import mcp_server; \
  print(mcp_server.mobile_balance())"
```

Rebuild the native optimizer after editing `native/beam_search.rs`:
`bash code/native/build.sh` (the planner falls back to pure Python if the dylib is
absent). More commands in [`docs/verification.md`](docs/verification.md).

## Known Issues

1. **Test coverage is partial.** `code/tests/` covers the pricing/flight-time
   formulas, the schedule builder, `db.py`, the mobile session and `mobile_login`.
   Everything behind CDP or the live mobile API is verified manually.
2. **`ALIASES` is duplicated** in `circuit_planner.py` and `aircraft_buyer.py`;
   keep them in sync.
3. **Native dylib is not committed.** Rebuild with `code/native/build.sh`.
4. **`aircraft_buyer.py` game-id table** may need a manual lookup for aircraft
   outside the current set.
5. **Mobile session expires every 3h.** The tools renew over HTTP and retry, so
   this should be invisible. If an auth error surfaces, `mobile_session_renew` (or
   `refresh_mobile_session.sh`) fixes it in seconds. Only missing OAuth material or
   changed credentials needs the BlueStacks/mitmproxy bootstrap; there, a token in
   the capture is **not** proof of a live session, so validate rather than grep.
