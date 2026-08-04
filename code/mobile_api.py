"""
Live Airlines Manager **mobile** (Tycoon) API client — separate from the
web/CDP surface. The mobile app exposes a JSON API the browser game does not:
the second-hand aircraft market (auction) and the daily login rewards (free
shop currency, travel-card wheel, slot machine).

These endpoints authenticate with an `access_token` (from the mobile login),
NOT the web session cookies — the web session gets 401 from them. So this
client talks to `www.airlines-manager.com/api/{player_id}/...` directly over
httpx with the stored mobile session.

Session secrets live at ~/.airlines_manager/session.json. Refresh with
`import_from_capture()` (reads the newest token from a mitmproxy capture of the
mobile app). There is no request signing — requests are replayable.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Iterator, Optional

import httpx

SESSION_PATH = Path.home() / ".airlines_manager" / "session.json"

# Match the app so traffic looks identical to a real client.
USER_AGENT = "UnityPlayer/2022.3.67f2 (UnityWebRequest/1.0, libcurl/8.10.1-DEV)"
# The app sends this alongside the UA on every call; keep the pair in sync if
# the client version ever moves.
UNITY_VERSION = "2022.3.67f2"
DEFAULT_BASE = "https://www.airlines-manager.com"

# The game caps how many aircraft you can have listed on the SHM at once.
# The 11th put_up is rejected with errorCode 170011 "Auction limit reached".
MAX_ACTIVE_LISTINGS = 10


class AMError(RuntimeError):
    """Any API-level failure (bad status field, HTTP error)."""


class AMAuthError(AMError):
    """Session expired / token rejected — re-import auth."""


class AMRateLimited(AMError):
    """Empty 204 response — action came in under a per-action cooldown."""


class AMAuctionLimit(AMError):
    """SHM auction limit (10 active listings) reached."""


@dataclass
class AMSession:
    base_url: str
    player_id: str
    access_token: str
    cookies: dict

    @classmethod
    def load(cls, path: Path = SESSION_PATH) -> "AMSession":
        if not path.exists():
            raise AMAuthError(
                f"No mobile session at {path}. Import one from a capture "
                "(mobile_session_import).")
        data = json.loads(path.read_text())
        return cls(
            base_url=data.get("base_url", DEFAULT_BASE),
            player_id=str(data["player_id"]),
            access_token=data["access_token"],
            cookies=data.get("cookies", {}),
        )

    def save(self, path: Path = SESSION_PATH) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(asdict(self), indent=2))
        os.chmod(path, 0o600)  # contains a live token


def import_from_capture(jsonl_path, base_url: str = DEFAULT_BASE) -> AMSession:
    """Build a session from the newest game request in a mitmproxy capture log."""
    from urllib.parse import urlsplit, parse_qs
    import http.cookies

    newest = None
    with open(jsonl_path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            r = json.loads(line)
            if "/api/" in r.get("path", "") and "access_token=" in r.get("path", ""):
                newest = r  # keep last (file is chronological)

    if not newest:
        raise AMError(f"No authenticated /api/ request found in {jsonl_path}")

    q = parse_qs(urlsplit(newest["path"]).query)
    token = q["access_token"][0]
    parts = urlsplit(newest["path"]).path.strip("/").split("/")
    player_id = parts[parts.index("api") + 1]

    cookies = {}
    raw_cookie = newest.get("req_headers", {}).get("Cookie", "")
    if raw_cookie:
        jar = http.cookies.SimpleCookie()
        jar.load(raw_cookie)
        for k, morsel in jar.items():
            cookies[k] = morsel.value

    return AMSession(base_url=base_url, player_id=player_id,
                     access_token=token, cookies=cookies)


class AMClient:
    """Thin wrapper over the mobile /api/{player_id}/... endpoints (httpx)."""

    def __init__(self, session: AMSession, min_delay: float = 0.0, store=None):
        self.s = session
        self.min_delay = min_delay
        self.store = store  # optional MobileStore; populated best-effort on reads
        # trust_env=False: don't route through a system proxy (Clash etc.),
        # matching cdp.py — a proxy breaks httpx here.
        self.http = httpx.Client(
            timeout=30, trust_env=False, cookies=dict(session.cookies),
            headers={"User-Agent": USER_AGENT,
                     "X-Unity-Version": UNITY_VERSION,
                     "X-Requested-With": "XMLHttpRequest"})
        self.last_resources: Optional[dict] = None
        self._last_call = 0.0

    def close(self):
        try:
            self.http.close()
        except Exception:
            pass

    # ── core ────────────────────────────────────────────────────────────
    def _url(self, endpoint: str) -> str:
        return f"{self.s.base_url}/api/{self.s.player_id}/{endpoint.lstrip('/')}"

    def _request(self, method: str, endpoint: str,
                 params: Optional[dict] = None,
                 data: Optional[dict] = None,
                 allow_empty: bool = False) -> dict:
        if self.min_delay:
            wait = self.min_delay - (time.monotonic() - self._last_call)
            if wait > 0:
                time.sleep(wait)
        params = dict(params or {})
        params["access_token"] = self.s.access_token
        url = self._url(endpoint)
        if method.upper() == "POST" and not data:
            # Empty POSTs (e.g. slot spins) need a zero-length form body with
            # Content-Type, else the server answers 204 No Content.
            resp = self.http.request(
                method, url, params=params, content=b"",
                headers={"Content-Type": "application/x-www-form-urlencoded"})
        else:
            resp = self.http.request(method, url, params=params, data=data)
        self._last_call = time.monotonic()

        # 204 / empty: a rate-limited action (slot/put_up under cooldown).
        if resp.status_code == 204 or not resp.content:
            if allow_empty:
                return {}
            raise AMRateLimited(f"{endpoint}: empty response ({resp.status_code})")

        ctype = resp.headers.get("Content-Type", "")
        if "application/json" not in ctype:
            raise AMAuthError(
                f"Non-JSON response ({resp.status_code}, {ctype}) from {endpoint} "
                "— session likely expired. Re-import the mobile session.")
        body = resp.json()
        status = body.get("status")
        if status in (0, "0", False):
            msg = str(body.get("fullErrorMessage")
                      or body.get("message", "unknown error"))
            low = msg.lower()
            if body.get("errorCode") == 170011 or "auction limit" in low:
                raise AMAuctionLimit(f"{endpoint}: {msg}")
            if (resp.status_code in (401, 403)
                    or any(w in low for w in ("auth", "invalid_grant", "grant",
                                              "token", "expired", "session"))):
                raise AMAuthError(
                    f"{endpoint}: {msg} — session expired. Re-import the mobile "
                    "session (open the app so it makes a fresh call, then import "
                    "the capture).")
            raise AMError(f"{endpoint}: status={status} message={msg}")
        if "ressources" in body:
            self.last_resources = body["ressources"]
        return body

    # ── reads ───────────────────────────────────────────────────────────
    def resources(self) -> dict:
        """Balances (dollar, amCoins, researchDollars, travelCards, allianceDollar)."""
        body = self._request("GET", "bfa/paged/aircraft",
                             params={"page": 0, "itemPerPage": 2,
                                     "purchaseType": "purchased",
                                     "withFrozenHubs": "false"})
        return body.get("ressources", {})

    def fleet_page(self, page: int = 1, per_page: int = 50,
                   purchase_type: str = "purchased",
                   sort: str = "nameMinus") -> dict:
        body = self._request("GET", "bfa/paged/aircraft",
                             params={"page": page, "itemPerPage": per_page,
                                     "purchaseType": purchase_type,
                                     "sorts[]": sort,
                                     "withFrozenHubs": "false"})
        if self.store:
            for it in body.get("aircraftList", []):
                self.store.observe_fleet_item(it)
            self.store.commit()
        return body

    def fleet(self, purchase_type: str = "purchased",
              per_page: int = 50, max_pages: Optional[int] = None
              ) -> Iterator[dict]:
        """Yield every owned aircraft (compact records), paging through.

        The API's `page` param is 1-BASED (page 0 aliases page 1), so we start
        at 1 and stop at pageCount — 0-based paging duplicates the first page
        and silently drops the last (where freshly-bought planes land).
        """
        page = 1
        while True:
            body = self.fleet_page(page, per_page, purchase_type)
            items = body.get("aircraftList", [])
            for it in items:
                yield it
            pg = body.get("pagination", {})
            last = pg.get("pageCount", pg.get("last", 1))
            if not items or page >= last:
                break
            if max_pages is not None and page >= max_pages:
                break
            page += 1

    def aircraft(self, aircraft_id: int) -> dict:
        profile = self._request("GET", f"aircraft/{aircraft_id}").get("profile", {})
        if self.store and profile:
            self.store.observe_aircraft_profile(profile)
            self.store.commit()
        return profile

    def auctions(self, sort: str = "timeMinus",
                 pool_only: bool = False) -> list:
        body = self._request("GET", "auction/aircraft/auction_list",
                             params={"sort": sort,
                                     "filterPoolOnly": str(pool_only).lower()})
        auctions = body.get("auctions", [])
        if self.store:
            for a in auctions:
                self.store.observe_auction(a)
            self.store.commit()
        return auctions

    def auction(self, auction_id: int) -> dict:
        return self._request("GET", f"auction/aircraft/auction/{auction_id}"
                             ).get("auction", {})

    def my_listings_count(self) -> Optional[int]:
        """How many auctions the player currently has active, if the API reports it."""
        body = self._request("GET", "auction/aircraft/auction_list",
                             params={"sort": "timeMinus",
                                     "filterPoolOnly": "false"})
        aa = body.get("airlineAuctions") or {}
        return aa.get("countAuction")

    def model_skins(self, model_id: int) -> list:
        """All liveries for a model (id, name, creator, Playrion status)."""
        skins = self._request("GET", f"shop/skin/{model_id}/getSkins"
                              ).get("skins", [])
        if self.store:
            for s in skins:
                self.store.upsert_skin(
                    s.get("id"), model_id=s.get("aircraftListId"),
                    name=s.get("name"), livery_type=s.get("type"),
                    creator=s.get("creator"), status=s.get("status"))
            self.store.commit()
        return skins

    # ── writes ──────────────────────────────────────────────────────────
    def put_up(self, aircraft_id: int, price: int, bin_price: int,
               duration: int = 11) -> dict:
        """List an owned aircraft on the second-hand market (auction).

        Raises AMAuctionLimit if the 10-listing cap is reached, AMRateLimited
        (204) if posted under the cooldown — a 204 does NOT list the plane.
        """
        body = self._request("POST", "auction/aircraft/put_up",
                             data={"price": int(price),
                                   "binPrice": int(bin_price),
                                   "duration": int(duration),
                                   "aircraftId": int(aircraft_id)})
        return body.get("auction", body)

    def bid(self, auction_id: int, amount: int) -> dict:
        """Place a bid (bidding the binPrice buys it outright)."""
        body = self._request("POST", "auction/aircraft/bid",
                             data={"auctionId": int(auction_id),
                                   "bid": int(amount)})
        return body.get("auction", body)

    def reconfigure(self, aircraft_id: int, *, name: str,
                    eco: int, bus: int, first: int, payload: int) -> dict:
        """Set an owned aircraft's seat/cargo configuration (a paid action).

        Seats are ABSOLUTE targets, not deltas. Returns the response dict; the
        server answers `status: 1, message: "aircraft.reconfigure.success"` on a
        real commit, so — unlike the web form — success is directly observable
        and `_request` already raises on `status: 0`.

        `name` is echoed back deliberately: the endpoint takes the name in the
        same payload and writes it, so passing the CURRENT name is what keeps a
        reconfigure from also renaming the aircraft. Callers must read it first.

        The livery is NOT part of this payload and is preserved across the call
        (verified: skin id survived a capture-confirmed reconfigure). That's why
        this has no equivalent of the web path's checked-skin safety guard.
        """
        return self._request("POST", "aircraft/reconfigure",
                             data={"aircraftId": int(aircraft_id),
                                   "name": name,
                                   "seatsEco": int(eco),
                                   "seatsBus": int(bus),
                                   "seatsFirst": int(first),
                                   "payload": int(payload)})

    def assign_hub(self, aircraft_id: int, hub_id: int) -> dict:
        """Relocate an owned aircraft to one of the player's hubs (paid).

        `hub_id` is the player's hub id (the same id space as the web side —
        `player_hubs.hub_id` resolves it from an IATA code). Answers
        `message: "aircraft.hubAssigned"` on success.

        The app posts aircraftID in the body as well as the path; kept so the
        request matches a real client byte for byte.
        """
        return self._request("POST", f"aircraft/{int(aircraft_id)}/assignHub",
                             data={"hubId": int(hub_id),
                                   "aircraftID": int(aircraft_id)})

    # ── daily: free shop currency ───────────────────────────────────────
    def shop_offers(self) -> list:
        return self._request("GET", "shop2023/offers").get("offers", [])

    def claim_offer(self, offer_id: int) -> dict:
        return self._request("POST", "shop2023/in-game/purchase/item",
                             data={"offerId": int(offer_id)})

    # ── daily: travel-card wheel ────────────────────────────────────────
    def wheel_rules(self) -> dict:
        return self._request("GET", "wheelTCGame/rules").get("rules", {})

    def wheel_play(self) -> dict:
        return self._request("POST", "wheelTCGame/play",
                             data={"ping": "pong"}).get("result", {})

    def wheel_replay(self) -> dict:
        """Second spin — server keeps the higher of the two scores."""
        return self._request("POST", "wheelTCGame/replay",
                             data={"ping": "pong"}).get("result", {})

    # ── daily: slot machine (cockpit à sous) ────────────────────────────
    def slot_rules(self) -> dict:
        return self._request("GET", "cockpitASous/rules").get("rules", {})

    def slot_play(self) -> dict:
        """One slot spin (empty POST). Bounded by nbRemainingGames by callers.

        Spins faster than the ~10s reel animation return {} (204) but still
        burn a free game — pace calls and do NOT retry an empty result.
        """
        return self._request("POST", "cockpitASous/play",
                             allow_empty=True).get("result", {})


# ── helpers ─────────────────────────────────────────────────────────────
def fmt_money(n: Any) -> str:
    try:
        n = float(n)
    except (TypeError, ValueError):
        return str(n)
    for unit, div in (("B", 1e9), ("M", 1e6), ("k", 1e3)):
        if abs(n) >= div:
            return f"${n / div:.2f}{unit}"
    return f"${n:.0f}"
