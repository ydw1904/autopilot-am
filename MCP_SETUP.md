# Airlines Manager MCP Server — Setup

The MCP server (`code/mcp_server.py`) exposes Airlines Manager game operations
as MCP tools so any agent (Claude Code, Hermes, etc.) can drive the game in
natural language. It works by attaching to a running Chrome over the Chrome
DevTools Protocol (CDP) and reusing that tab's logged-in session.

## 1. Python environment

Dependencies are listed in `code/requirements.txt` (`mcp`, `httpx`,
`websocket-client`, `numpy`, `colorama`). Install them into the project venv:

```bash
cd /Users/dawei/lobster-shared/autopilot-am
python3 -m venv .venv                       # if .venv doesn't exist yet
.venv/bin/pip install -r code/requirements.txt
```

Verify the server boots and registers its tools:

```bash
.venv/bin/python -c "import asyncio,sys; sys.path.insert(0,'code'); import mcp_server; \
print(len(asyncio.run(mcp_server.mcp.list_tools())), 'tools')"
# -> 24 tools
```

## 2. Chrome with remote debugging

The server attaches to whichever Chrome tab is on `airlines-manager.com`. Start
Chrome with debugging enabled and log in:

```bash
code/launch_chrome.sh
# equivalent to:
#   "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" \
#     --remote-debugging-port=9222 --remote-allow-origins=* \
#     --user-data-dir="$HOME/.am-chrome-profile" \
#     https://www.airlines-manager.com/network/planning
```

Log in to Airlines Manager in that window and leave the tab open. The login
cookie persists in the `--user-data-dir` profile, so you only log in once.

## 3. Register the server with your agent

### Claude Code (this repo)

`.mcp.json` at the repo root already declares the server, pointing at the venv
interpreter and the absolute path to `mcp_server.py`:

```json
{
  "mcpServers": {
    "airlines-manager": {
      "command": "/Users/dawei/lobster-shared/autopilot-am/.venv/bin/python",
      "args": ["/Users/dawei/lobster-shared/autopilot-am/code/mcp_server.py"]
    }
  }
}
```

Claude Code picks up `.mcp.json` from the project root on launch. Approve the
server when prompted, then `/mcp` lists its tools. Use absolute paths — the
server is launched from an arbitrary working directory.

### Hermes (`~/.hermes/config.yaml`)

```yaml
mcp_servers:
  airlines-manager:
    command: "/Users/dawei/lobster-shared/autopilot-am/.venv/bin/python"
    args: ["/Users/dawei/lobster-shared/autopilot-am/code/mcp_server.py"]
```

## 4. Smoke test the full stack

With Chrome up and logged in:

```bash
.venv/bin/python -c "import sys; sys.path.insert(0,'code'); import mcp_server; \
print(mcp_server.get_balance())"
```

A dollar balance means CDP attachment and the session cookie both work. If you
get "No Airlines Manager tab found," Chrome isn't running with the debug port or
no tab is on airlines-manager.com.

## Tools at a glance

24 tools. The mutating ones default to `dry_run=True`.

- **Read / live CDP:** `get_balance`, `list_hubs`, `list_routes`,
  `get_aircraft_at_hub`, `list_aircraft_for_sale`, `get_page_text`,
  `navigate_to`
- **Direct game actions (CDP):** `buy_route`, `schedule_flight`
- **Script wrappers (subprocess):** `plan_circuits`, `buy_circuit_routes`,
  `buy_aircraft`, `schedule_circuits`, `auto_price_routes`,
  `refresh_internal_audits`, `sync_warehouse`, `get_masstool_data`,
  `number_circuit_aircraft`, `reconfigure_circuit_aircraft`, `rename_circuit`,
  `mass_rename_aircraft`, `mass_unschedule_aircraft`, `scrape_line_ids`,
  `scrape_audit_line_ids`

`buy_route` uses the country-listing flow with native `form.submit()` (ported
from `circuit_route_buyer.py`) — the game silently rejects `fetch()`-based
purchase POSTs, so the older fetch flow could report success without buying.
