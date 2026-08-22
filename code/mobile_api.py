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
# The OAuth2 token endpoint lives on a different host from the game API.
AUTH_BASE = "https://auth.airlines-manager.com"
# The app posts the token endpoint as /oauth/v2/token?version=40008 — a QUERY
# param, not a body field, and it is load-bearing: omit it and the grant still
# returns HTTP 200 with a usable-looking token, but the session is stamped
# `version: 0` and the newer endpoints refuse it ("Invalid or missing
# parameter" / error 10205 on bfa/paged/aircraft, "Update your game to access
# the new secondhand market features" on the auctions). Bump this when the app
# does; the value is visible in the app's own oauth request.
APP_VERSION = 40008

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


def _token_request(data: dict, auth_base: str = AUTH_BASE) -> dict:
    """POST the OAuth2 token endpoint. Raises AMAuthError on any refusal."""
    with httpx.Client(timeout=30, trust_env=False,
                      headers={"User-Agent": USER_AGENT,
                               "X-Unity-Version": UNITY_VERSION}) as c:
        resp = c.post(f"{auth_base}/oauth/v2/token",
                      params={"version": APP_VERSION}, data=data)
    try:
        body = resp.json()
    except Exception:
        raise AMAuthError(
            f"token endpoint returned {resp.status_code}, non-JSON") from None
    if resp.status_code != 200 or "access_token" not in body:
        raise AMAuthError(
            f"{data.get('grant_type')} grant refused ({resp.status_code}): "
            f"{body.get('fullErrorMessage') or body.get('message', body)}")
    # A version-0 session looks fine here and then fails obscurely several
    # calls later, so refuse it at the source rather than shipping a token
    # that can read bfa/hub but not bfa/paged/aircraft.
    if "version" in body and not body["version"]:
        raise AMAuthError(
            f"grant returned version={body['version']!r} — the ?version= query "
            f"param did not take (expected {APP_VERSION}). The token would be "
            "rejected by the paged-fleet and auction endpoints.")
    return body


@dataclass
class AMSession:
    base_url: str
    player_id: str
    access_token: str
    cookies: dict
    # ── OAuth material, all optional so pre-existing session.json files load ──
    # With these present the session renews itself over HTTP and the whole
    # BlueStacks/mitmproxy/adb rig is only ever needed once, to bootstrap them.
    refresh_token: Optional[str] = None
    client_id: Optional[str] = None
    client_secret: Optional[str] = None
    device_id: Optional[str] = None
    locale: str = "en"
    username: Optional[str] = None
    password: Optional[str] = None
    expires_at: Optional[float] = None  # unix seconds

    @classmethod
    def load(cls, path: Path = SESSION_PATH) -> "AMSession":
        if not path.exists():
            raise AMAuthError(
                f"No mobile session at {path}. Import one from a capture "
                "(mobile_session_import).")
        data = json.loads(path.read_text())
        known = {f for f in cls.__dataclass_fields__}
        kwargs = {k: v for k, v in data.items() if k in known}
        kwargs["base_url"] = data.get("base_url", DEFAULT_BASE)
        kwargs["player_id"] = str(data["player_id"])
        kwargs.setdefault("cookies", {})
        return cls(**kwargs)

    def save(self, path: Path = SESSION_PATH) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(asdict(self), indent=2))
        os.chmod(path, 0o600)  # live token, refresh token, maybe the password

    # ── self-renewal ────────────────────────────────────────────────────
    @property
    def can_renew(self) -> bool:
        return bool(self.client_id and self.client_secret
                    and (self.refresh_token or (self.username and self.password)))

    def _absorb(self, body: dict) -> None:
        self.access_token = body["access_token"]
        # The refresh token is SINGLE-USE and rotates on every grant — the
        # server answers "Invalid refresh token" (errorCode 10) if an old one is
        # replayed. Persisting the new one immediately is what keeps the chain
        # alive; drop it once and only the password grant can recover.
        if body.get("refresh_token"):
            self.refresh_token = body["refresh_token"]
        if body.get("expires_in"):
            self.expires_at = time.time() + int(body["expires_in"])
        if body.get("airlineId"):
            self.player_id = str(body["airlineId"])
        if body.get("gameServer"):
            self.base_url = f"https://{body['gameServer']}"

    def renew(self, save: bool = True) -> str:
        """Mint a fresh access_token over HTTP. Returns the grant used.

        Tries the refresh token first (so a stored password isn't required),
        then falls back to the password grant. Tokens live 3h, so this runs
        far more often than the old ~daily capture assumed.
        """
        if not (self.client_id and self.client_secret):
            raise AMAuthError(
                "no OAuth client credentials stored — bootstrap once with "
                "mobile_session_import from a capture, which now records them.")
        errors = []
        if self.refresh_token:
            try:
                self._absorb(_token_request({
                    "grant_type": "refresh_token",
                    "refresh_token": self.refresh_token,
                    "client_id": self.client_id,
                    "client_secret": self.client_secret}))
                if save:
                    self.save()
                return "refresh_token"
            except AMAuthError as e:
                # Expired or already consumed. Clear it so we don't retry a
                # known-dead token on every call.
                errors.append(f"refresh_token: {e}")
                self.refresh_token = None
        if self.username and self.password:
            self._absorb(_token_request({
                "grant_type": "password",
                "username": self.username, "password": self.password,
                "client_id": self.client_id, "client_secret": self.client_secret,
                "device_id": self.device_id or "", "locale": self.locale}))
            if save:
                self.save()
            return "password"
        raise AMAuthError(
            "could not renew: " + "; ".join(errors or ["no refresh token"])
            + ". No stored password to fall back on — re-bootstrap from a "
              "capture (refresh_mobile_session.sh).")


def import_from_capture(jsonl_path, base_url: str = DEFAULT_BASE) -> AMSession:
    """Build a session from the newest game request in a mitmproxy capture log.

    Also harvests the OAuth material from any `oauth/v2/token` exchange in the
    same capture (client id/secret, device id, the credentials the app posted,
    and the returned refresh token). That is what lets the session renew itself
    over HTTP afterwards — so this capture-based bootstrap is a ONE-TIME step
    rather than a daily chore.
    """
    from urllib.parse import urlsplit, parse_qs
    import http.cookies

    newest = None
    oauth_req = oauth_res = None
    with open(jsonl_path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            r = json.loads(line)
            if "/api/" in r.get("path", "") and "access_token=" in r.get("path", ""):
                newest = r  # keep last (file is chronological)
            elif "oauth/v2/token" in r.get("path", "") and r.get("status") == 200:
                try:
                    oauth_req = parse_qs(r.get("req_body") or "")
                    oauth_res = json.loads(r.get("res_body") or "{}")
                except Exception:
                    oauth_req = oauth_res = None

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

    sess = AMSession(base_url=base_url, player_id=player_id,
                     access_token=token, cookies=cookies)

    if oauth_req and oauth_res:
        one = lambda k: (oauth_req.get(k) or [None])[0]  # noqa: E731
        sess.client_id = one("client_id")
        sess.client_secret = one("client_secret")
        sess.device_id = one("device_id")
        sess.locale = one("locale") or "en"
        sess.username = one("username")
        sess.password = one("password")
        # Prefer the token pair from the oauth exchange itself: it is newer than
        # anything scraped off a URL, and carries the refresh token.
        if oauth_res.get("access_token"):
            sess._absorb(oauth_res)
    return sess


class AMClient:
    """Thin wrapper over the mobile /api/{player_id}/... endpoints (httpx)."""

    def __init__(self, session: AMSession, min_delay: float = 0.0, store=None,
                 auto_renew: bool = True):
        self.s = session
        self.min_delay = min_delay
        self.store = store  # optional MobileStore; populated best-effort on reads
        self.auto_renew = auto_renew
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
        """Perform a call, renewing the session once if the token has died.

        Access tokens live 3h, so a long-running job (a 2.8k-aircraft sweep,
        the daily routine) can easily start valid and expire mid-flight. When
        the session carries OAuth material it renews in-process and retries;
        otherwise the AMAuthError propagates as before.
        """
        try:
            return self._request_once(method, endpoint, params, data, allow_empty)
        except AMAuthError:
            if not (self.auto_renew and self.s.can_renew):
                raise
            self.s.renew()
            self.http.cookies.update(self.s.cookies)
            return self._request_once(method, endpoint, params, data, allow_empty)

    def _request_once(self, method: str, endpoint: str,
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

    def skin_catalog(self) -> list:
        """The client's boot-time livery manifest: [{id, picturePath}, ...].

        NOT an ownership list — it omits some liveries the player owns and
        includes the current event's. Its value is that it carries a CDN
        picturePath for every id in it, which is what the artwork fetch needs.
        """
        skins = self._request("GET", "bfa/aircraft/skin").get("aircraftSkinList", [])
        if self.store:
            for s in skins:
                pic = (s.get("picturePath") or {})
                self.store.upsert_skin(
                    s.get("id"), picture_path=(pic.get("big")
                                               or pic.get("medium") or "").split("?")[0] or None)
            self.store.commit()
        return skins

    # ── boosters ────────────────────────────────────────────────────────
    def boosters(self) -> list:
        """Active booster packs (id, name, window, pity gauge, prices)."""
        body = self._request("GET", "booster")
        out = body.get("boosters", [])
        if self.store:
            for b in out:
                self.store.upsert_booster(b)
            self.store.commit()
        return out

    def booster_droprate(self, booster_id: int) -> dict:
        """The published drop table for one booster.

        Found by capturing the app on the booster contents screen — it is not
        reachable by guessing (`booster/rates`, `booster/probabilities` and the
        other obvious spellings all answer errorCode 99). Rates are published
        per RARITY GROUP, not per card; each card carries a full `skin` object
        ({id, name, picturePath}), which is the only source of livery names for
        skins the player does not own.
        """
        body = self._request("GET", "booster/droprate",
                             params={"boosterId": int(booster_id)})
        if self.store:
            self.store.record_droprate(int(booster_id), body)
            self.store.commit()
        return body

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

# Livery artwork lives on the game's CDN, not the API. www.airlines-manager.com
# 301s to CloudFront; no auth, no token, so this needs a plain client rather
# than AMClient's authenticated one.
SKIN_IMAGE_SIZES = ("medium", "big", "superBig")


def skin_image_client() -> httpx.Client:
    """A keep-alive client for the CDN. Hundreds of liveries means hundreds of
    requests; one connection beats a TLS handshake per PNG."""
    return httpx.Client(timeout=30, trust_env=False, follow_redirects=True,
                        headers={"User-Agent": USER_AGENT})


def fetch_skin_png(picture_path: str, size: str = "big",
                   base_url: str = DEFAULT_BASE,
                   client: Optional[httpx.Client] = None) -> tuple:
    """Download one livery PNG. Returns (bytes, final_url).

    `picture_path` is any of the sizes the API hands back; the size segment is
    swapped for the one requested. Pass `client` (see skin_image_client) to
    reuse a connection across a batch.
    """
    if size not in SKIN_IMAGE_SIZES:
        raise ValueError(f"size must be one of {SKIN_IMAGE_SIZES}")
    path = picture_path.split("?")[0]
    for s in SKIN_IMAGE_SIZES:
        path = path.replace(f"/skins/{s}/", f"/skins/{size}/")
    owned = client is None
    c = client or skin_image_client()
    try:
        r = c.get(base_url.rstrip("/") + path)
        r.raise_for_status()
        data = r.content
    finally:
        if owned:
            c.close()
    if not data.startswith(b"\x89PNG"):
        raise AMError(f"{path}: not a PNG (got {data[:16]!r})")
    return data, str(r.url)
