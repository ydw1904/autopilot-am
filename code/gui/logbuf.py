import threading
from datetime import datetime

_lock = threading.Lock()
_entries: list[tuple[str, str, str]] = []  # (time, source, message)
_MAX = 1000


def add(source: str, msg: str) -> None:
    ts = datetime.now().strftime("%H:%M:%S")
    with _lock:
        _entries.append((ts, source, msg))
        if len(_entries) > _MAX:
            del _entries[:-_MAX]


def get_all() -> list[tuple[str, str, str]]:
    with _lock:
        return list(_entries)


def clear() -> None:
    with _lock:
        _entries.clear()
