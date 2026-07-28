"""
mitmproxy addon — capture Airlines Manager (Tycoon mobile) API traffic.

Run it in place of mitmweb (device already proxies to :8080):

    mitmdump -s tools/capture_am.py --listen-host 0.0.0.0 -p 8080

Every request/response to *.airlines-manager.com (plus the AWS lambda the app
uses) is appended as one JSON object per line to  tools/captures/am_api.jsonl
and summarized live on stdout. Non-game hosts (ads, analytics, google) are
ignored so the log stays readable.

Read the JSONL afterwards to map endpoints, auth, and payload shapes.
"""

from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone

from mitmproxy import http

# Hosts we care about — the game backend and the crypto lambda.
INTERESTING_SUFFIXES = (
    "airlines-manager.com",
    "lambda-url.eu-west-1.on.aws",
)

OUT_DIR = os.path.join(os.path.dirname(__file__), "captures")
OUT_FILE = os.path.join(OUT_DIR, "am_api.jsonl")
os.makedirs(OUT_DIR, exist_ok=True)

# Response bodies larger than this are truncated in the log (keeps it sane).
MAX_BODY = 200_000


def _wanted(host: str) -> bool:
    return any(host == s or host.endswith("." + s) or host.endswith(s)
               for s in INTERESTING_SUFFIXES)


def _decode_body(msg) -> tuple[str, bool]:
    """Return (text, is_truncated). mitmproxy already gunzips .text/.content."""
    try:
        raw = msg.content or b""
    except Exception:
        raw = b""
    truncated = len(raw) > MAX_BODY
    raw = raw[:MAX_BODY]
    try:
        return raw.decode("utf-8", "replace"), truncated
    except Exception:
        return repr(raw), truncated


def response(flow: http.HTTPFlow) -> None:
    host = flow.request.pretty_host
    if not _wanted(host):
        return

    req_body, req_trunc = _decode_body(flow.request)
    res_body, res_trunc = _decode_body(flow.response) if flow.response else ("", False)

    entry = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "method": flow.request.method,
        "scheme": flow.request.scheme,
        "host": host,
        "path": flow.request.path,          # includes query string
        "status": flow.response.status_code if flow.response else None,
        "req_headers": dict(flow.request.headers),
        "req_body": req_body,
        "req_body_truncated": req_trunc,
        "res_headers": dict(flow.response.headers) if flow.response else {},
        "res_body": res_body,
        "res_body_truncated": res_trunc,
    }

    with open(OUT_FILE, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, ensure_ascii=False) + "\n")

    # live one-liner
    status = entry["status"]
    blen = len(res_body)
    print(f"[AM] {entry['method']:4} {status} {host}{flow.request.path[:80]}  ({blen}B)",
          flush=True)


def load(loader):
    print(f"[AM capture] logging {', '.join(INTERESTING_SUFFIXES)} -> {OUT_FILE}",
          flush=True)
