"""Canonical aircraft-name resolver — the single source of truth for aliases.

Any reasonable spelling an agent or user might type ("A380", "A388", "A380-800",
"a380 800") resolves to the same canonical model string the scripts expect. The
index is derived from the `aircraft` table (model + icao_code), so it stays in sync
with the game data automatically; the curated overlay below is only for irregular
nicknames the DB can't supply.

Resolution distinguishes three outcomes so callers never silently buy the wrong jet:
  ok        — a single canonical model (carries model + icao)
  ambiguous — input maps to several models (carries candidates); caller must choose
  not_found — nothing matched (carries nearest suggestions)
"""

import difflib
import re
import sqlite3
import sys
from dataclasses import dataclass, field

from db import DB

# Irregular nicknames the DB can't supply. Bare family names ("A380", "747") are
# handled generically by prefix matching, so this is intentionally empty for now —
# add only colloquialisms with no canonical/ICAO form (key -> canonical model).
_OVERLAY: dict[str, str] = {}


def _norm(s: str) -> str:
    """Uppercase and strip spaces/hyphens/dots/underscores/slashes."""
    return re.sub(r"[\s\-._/]", "", (s or "")).upper()


@dataclass
class Resolution:
    status: str                 # "ok" | "ambiguous" | "not_found"
    query: str
    model: str | None = None    # canonical model, when status == "ok"
    icao: str | None = None     # its icao_code, when status == "ok"
    candidates: list[dict] = field(default_factory=list)   # [{"model","icao"}] when ambiguous
    suggestions: list[dict] = field(default_factory=list)  # nearest matches when not_found


@dataclass
class _Index:
    exact: dict[str, set]       # normalized model/icao key -> set of canonical models
    by_model: dict[str, str]    # normalized model key -> canonical model
    model_to_icao: dict[str, str]


_cache: dict[str, _Index] = {}


def _build(path: str) -> _Index:
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        rows = conn.execute("SELECT model, icao_code FROM aircraft").fetchall()
    finally:
        conn.close()

    exact: dict[str, set] = {}
    by_model: dict[str, str] = {}
    model_to_icao: dict[str, str] = {}
    for model, icao in rows:
        if not model:
            continue
        model_to_icao[model] = icao
        nm = _norm(model)
        by_model.setdefault(nm, model)
        exact.setdefault(nm, set()).add(model)
        if icao:
            exact.setdefault(_norm(icao), set()).add(model)

    for alias, target in _OVERLAY.items():
        if target in model_to_icao:
            exact.setdefault(_norm(alias), set()).add(target)
        else:
            print(f"[aircraft_aliases] overlay target not in DB, skipping: "
                  f"{alias!r} -> {target!r}", file=sys.stderr)

    return _Index(exact, by_model, model_to_icao)


def _index(db_path: str | None = None) -> _Index:
    path = db_path or DB
    if path not in _cache:
        _cache[path] = _build(path)
    return _cache[path]


def reset_cache() -> None:
    """Drop the cached index (used by tests after pointing at a fixture DB)."""
    _cache.clear()


def _cand(idx: _Index, model: str) -> dict:
    return {"model": model, "icao": idx.model_to_icao.get(model)}


def resolve(query: str, db_path: str | None = None) -> Resolution:
    """Resolve any aircraft spelling to a canonical model. See module docstring."""
    q = query or ""
    key = _norm(q)
    if not key:
        return Resolution("not_found", query=q)

    idx = _index(db_path)

    # 1. Exact normalized match on a model or ICAO code (exact wins over prefix).
    if key in idx.exact:
        models = sorted(idx.exact[key])
        if len(models) == 1:
            m = models[0]
            return Resolution("ok", query=q, model=m, icao=idx.model_to_icao.get(m))
        return Resolution("ambiguous", query=q,
                          candidates=[_cand(idx, m) for m in models])

    # 2. Prefix match over model names — gives generic family support.
    pref = sorted({idx.by_model[k] for k in idx.by_model if k.startswith(key)})
    if len(pref) == 1:
        m = pref[0]
        return Resolution("ok", query=q, model=m, icao=idx.model_to_icao.get(m))
    if len(pref) > 1:
        return Resolution("ambiguous", query=q,
                          candidates=[_cand(idx, m) for m in pref])

    # 3. Nothing matched — offer nearest models as suggestions.
    matches = difflib.get_close_matches(key, list(idx.by_model.keys()), n=5, cutoff=0.7)
    return Resolution("not_found", query=q,
                      suggestions=[_cand(idx, idx.by_model[k]) for k in matches])


def canonical(query: str, db_path: str | None = None) -> str | None:
    """Convenience: canonical model string on a unique hit, else None."""
    r = resolve(query, db_path)
    return r.model if r.status == "ok" else None
