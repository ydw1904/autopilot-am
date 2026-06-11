import subprocess
import time


class OpenClawBackend:
    name = "openclaw"

    def navigate(self, url):
        try:
            subprocess.run(
                ["openclaw", "browser", "navigate", url],
                capture_output=True, text=True, timeout=15,
            )
        except Exception:
            pass
        time.sleep(3)

    def eval_js(self, expression, timeout=15):
        try:
            r = subprocess.run(
                ["openclaw", "browser", "evaluate", "--fn", expression],
                capture_output=True, text=True, timeout=timeout,
            )
            return r.stdout.strip().strip('"')
        except subprocess.TimeoutExpired:
            return "TIMEOUT"
        except Exception as e:
            return f"ERROR:{e}"

    def close(self):
        pass

    @classmethod
    def is_available(cls):
        try:
            r = subprocess.run(
                ["openclaw", "--version"],
                capture_output=True, text=True, timeout=5,
            )
            return r.returncode == 0
        except Exception:
            return False


class CDPBackend:
    name = "cdp"

    def __init__(self, port=9222):
        self.port = port
        self._ws = None
        self._send = None
        self._msg_id = 1
        self._connect()

    def _connect(self):
        import json
        import httpx
        import websocket as ws_lib

        tabs = json.loads(
            httpx.get(f"http://localhost:{self.port}/json", timeout=5).text
        )
        ws_url = None
        for t in tabs:
            if t.get("type") == "page" and "airlines-manager.com" in t.get("url", ""):
                ws_url = t.get("webSocketDebuggerUrl")
                break

        if not ws_url:
            resp = httpx.put(
                f"http://localhost:{self.port}/json/new?https://www.airlines-manager.com/network/newline",
                timeout=10,
            )
            tab = resp.json()
            ws_url = tab.get("webSocketDebuggerUrl")
            time.sleep(3)

        self._ws = ws_lib.create_connection(ws_url, timeout=60)
        self._ws.settimeout(30)
        self._send_raw("Page.enable")
        time.sleep(0.5)

    def _send_raw(self, method, params=None):
        import json
        msg = {"id": self._msg_id, "method": method}
        if params:
            msg["params"] = params
        self._ws.send(json.dumps(msg))
        self._msg_id += 1

        while True:
            resp = json.loads(self._ws.recv())
            if resp.get("id") == self._msg_id - 1:
                return resp

    def navigate(self, url):
        self._send_raw("Page.navigate", {"url": url})
        time.sleep(3)

    def eval_js(self, expression, timeout=15):
        try:
            resp = self._send_raw("Runtime.evaluate", {
                "expression": expression,
                "returnByValue": True,
                "awaitPromise": False,
            })
            exc = resp.get("result", {}).get("exceptionDetails")
            if exc:
                return f"ERROR:JS {exc.get('text', 'exception')}"
            return resp.get("result", {}).get("result", {}).get("value", "")
        except Exception as e:
            return f"ERROR:{e}"

    def eval_js_await(self, expression, timeout=30):
        try:
            resp = self._send_raw("Runtime.evaluate", {
                "expression": expression,
                "returnByValue": True,
                "awaitPromise": True,
            })
            return resp.get("result", {}).get("result", {}).get("value", "")
        except Exception as e:
            return f"ERROR:{e}"

    def close(self):
        if self._ws:
            try:
                self._ws.close()
            except Exception:
                pass
            self._ws = None

    @classmethod
    def is_available(cls, port=9222):
        try:
            import httpx
            httpx.get(f"http://localhost:{port}/json", timeout=2)
            return True
        except Exception:
            return False
