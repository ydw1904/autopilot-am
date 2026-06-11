"""Shared Chrome DevTools Protocol client for all Airlines Manager tools.

Single home for the CDP WebSocket client, the AM-tab finder, and the
project-wide constants every script needs. Importing this module also strips
proxy env vars from the process — CDP is always localhost and routing it
through Clash/etc. breaks both httpx and websocket-client.

Code evaluated via CDP runs IN the page's context, so it has access to all
cookies, session state, and DOM elements — same as typing it in the DevTools
console. Requires Chrome started with:
    --remote-debugging-port=9222 --remote-allow-origins=*
"""

import json
import os
import sys
import time

for _k in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY",
           "http_proxy", "https_proxy", "all_proxy"):
    os.environ.pop(_k, None)

import httpx
import websocket

CDP_URL = "http://localhost:9222"
BASE_URL = "https://www.airlines-manager.com"


class CDP:
    """Manages a single persistent WebSocket connection to Chrome DevTools.

    Sends JSON-RPC commands (method + params) with incrementing IDs and reads
    responses until one matches the sent ID.
    """

    def __init__(self, ws_url, timeout=30):
        self.ws_url = ws_url
        self.timeout = timeout
        self.ws = None
        self._msg_id = 0

    def connect(self):
        self.ws = websocket.create_connection(
            self.ws_url, timeout=self.timeout,
            origin="http://localhost:9222", suppress_origin=False,
        )

    def close(self):
        if self.ws:
            self.ws.close()

    def _send(self, method, params=None):
        self._msg_id += 1
        cmd = {"id": self._msg_id, "method": method}
        if params:
            cmd["params"] = params
        self.ws.send(json.dumps(cmd))
        return self._msg_id

    def _recv(self, target_id):
        while True:
            try:
                resp = json.loads(self.ws.recv())
            except websocket.WebSocketTimeoutException:
                return None
            if resp.get("id") == target_id:
                return resp
            self._msg_id = max(self._msg_id, resp.get("id", 0))

    def eval(self, expression, await_promise=False):
        """Run JavaScript in the page and return the result value.

        await_promise=True is required for fetch() calls — without it you get
        the unresolved Promise object instead of the data.
        """
        mid = self._send("Runtime.evaluate", {
            "expression": expression,
            "returnByValue": True,
            "awaitPromise": await_promise,
        })
        resp = self._recv(mid)
        if not resp:
            return None
        exc = resp.get("result", {}).get("exceptionDetails")
        if exc:
            desc = exc.get("exception", {}).get("description", "unknown")
            print(f"  JS error: {desc[:200]}", file=sys.stderr)
            return None
        return resp.get("result", {}).get("result", {}).get("value")

    def eval_json(self, expression, await_promise=False):
        """Like eval() but auto-parses JSON strings into dicts/lists."""
        val = self.eval(expression, await_promise=await_promise)
        if val is None:
            return None
        if isinstance(val, (dict, list)):
            return val
        if isinstance(val, str):
            try:
                return json.loads(val)
            except (json.JSONDecodeError, TypeError):
                return None
        return val

    def navigate(self, url):
        # AM installs a `beforeunload` handler that aborts the first
        # programmatic navigation (Chrome auto-dismisses the prompt under CDP
        # → net::ERR_ABORTED). Retry: the first attempt consumes the handler.
        for _ in range(6):
            mid = self._send("Page.navigate", {"url": url})
            resp = self._recv(mid)
            if not resp:
                return
            err = resp.get("result", {}).get("errorText")
            if not err or err != "net::ERR_ABORTED":
                return
            time.sleep(0.2)

    def wait(self, seconds):
        time.sleep(seconds)


def get_am_tab():
    """Find an existing Airlines Manager tab via the CDP HTTP endpoint."""
    # trust_env=False prevents httpx from routing localhost through any
    # configured HTTP/SOCKS proxy (e.g. Clash).
    with httpx.Client(timeout=10, trust_env=False) as client:
        resp = client.get(f"{CDP_URL}/json")
        resp.raise_for_status()
        for tab in resp.json():
            if "airlines-manager.com" in tab.get("url", ""):
                return tab
    return None


def connect_cdp():
    """Connect to the AM tab's CDP websocket, or exit with an error."""
    tab = get_am_tab()
    if not tab:
        print("ERROR: No Airlines Manager tab found.", file=sys.stderr)
        print("Open AM in Chrome with --remote-debugging-port=9222", file=sys.stderr)
        sys.exit(1)
    cdp = CDP(tab["webSocketDebuggerUrl"])
    cdp.connect()
    return cdp
