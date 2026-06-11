"""Shared async workers: subprocess streaming, Chrome/CDP launch."""

import asyncio
import http.client
import json
import os
import re
import subprocess
import time
from typing import Callable

import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from gui.logbuf import add as log_add

_ANSI = re.compile(r'\x1b\[[0-9;]*m')
_PROGRESS = re.compile(
    r'\[\s*\d+/\d+\]'           # numberer: [  3/99]
    r'|[A-Z]+-C\d{3}-\d{3}\s',  # scheduler: MPM-C006-001
)

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CODE_DIR  = os.path.join(REPO_ROOT, 'code')


def cdp_up() -> bool:
    try:
        conn = http.client.HTTPConnection('127.0.0.1', 9222, timeout=2)
        conn.request('GET', '/json')
        conn.getresponse()
        return True
    except Exception:
        return False


async def run_subprocess(
    script_name: str,
    args: list[str],
    on_progress: Callable[[str], None],
    needs_cdp: bool = True,
) -> tuple[int, list[str]]:
    """Run a script, stream lines, call on_progress for matching lines.
    Returns (returncode, all_lines)."""
    if needs_cdp and not cdp_up():
        msg = 'CDP not available — use Launch Chrome first'
        log_add(script_name, msg)
        on_progress(msg)
        return -1, [msg]

    script = os.path.join(CODE_DIR, script_name)
    cmd = ['python3', script, *args]
    log_add(script_name, f"$ {' '.join(args)}")

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    all_lines: list[str] = []
    async for raw in proc.stdout:
        clean = _ANSI.sub('', raw.decode()).strip()
        if clean:
            log_add(script_name, clean)
            all_lines.append(clean)
            if _PROGRESS.search(clean):
                on_progress(clean[:120])
    await proc.wait()
    return proc.returncode, all_lines


async def launch_chrome(on_status: Callable[[str], None]) -> None:
    """Launch Chrome with CDP or verify it's already up, then open AM tab."""

    def cdp_get(path: str):
        conn = http.client.HTTPConnection('127.0.0.1', 9222, timeout=2)
        conn.request('GET', path)
        return json.loads(conn.getresponse().read())

    def cdp_put(path: str):
        conn = http.client.HTTPConnection('127.0.0.1', 9222, timeout=10)
        conn.request('PUT', path, body=b'')
        return json.loads(conn.getresponse().read())

    def ensure_am_tab():
        try:
            tabs = cdp_get('/json')
            for tab in tabs:
                if 'airlines-manager.com' in tab.get('url', ''):
                    return 'CDP ready — AM tab already open'
            cdp_put('/json/new?https://www.airlines-manager.com/network/planning')
            return 'CDP ready — opened AM tab'
        except Exception as e:
            return f'CDP up but AM tab failed: {e}'

    if cdp_up():
        msg = await asyncio.get_event_loop().run_in_executor(None, ensure_am_tab)
        log_add('chrome', msg)
        on_status(msg)
        return

    chrome = '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'
    if not os.path.exists(chrome):
        msg = 'Chrome not found at /Applications/Google Chrome.app'
        log_add('chrome', msg)
        on_status(msg)
        return

    already = await asyncio.get_event_loop().run_in_executor(
        None,
        lambda: subprocess.run(['pgrep', '-x', 'Google Chrome'],
                               capture_output=True).returncode == 0
    )
    if already:
        msg = ('Chrome is already running without --remote-debugging-port. '
               'Quit Chrome (⌘Q) then press Launch Chrome again.')
        log_add('chrome', msg)
        on_status(msg)
        return

    log_add('chrome', f'launching {chrome}')
    on_status('launching Chrome…')
    subprocess.Popen(
        [chrome, '--remote-debugging-port=9222', '--remote-allow-origins=*',
         f'--user-data-dir={os.path.expanduser("~/.am-chrome")}'],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )

    for _ in range(60):
        await asyncio.sleep(0.5)
        if cdp_up():
            msg = await asyncio.get_event_loop().run_in_executor(None, ensure_am_tab)
            log_add('chrome', msg)
            on_status(msg)
            return

    msg = 'CDP unreachable after 30s — something went wrong launching Chrome.'
    log_add('chrome', msg)
    on_status(msg)
