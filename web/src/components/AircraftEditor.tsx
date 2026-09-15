import React, { useCallback, useEffect, useId, useMemo, useState } from "react";
import {
  AlertTriangle,
  ArrowLeft,
  Building2,
  CalendarDays,
  Check,
  Flame,
  Gavel,
  History,
  Layers,
  Palette,
  Plane,
  RefreshCcw,
  Search,
  Tag,
  Wrench,
  X,
} from "lucide-react";
import {
  fetchFleetNameSuggestions,
  fetchFleetPage,
  fetchHangarAircraft,
  fetchHangarLiveries,
  fetchHangarSchedule,
  listHangarAircraft,
  moveHangarAircraft,
  paintHangarAircraft,
  reconfigureHangarAircraft,
  renameHangarAircraft,
  scrapHangarAircraft,
  unscheduleHangarAircraft,
} from "../api";
import {
  CommandCenterSnapshot,
  FleetAircraft,
  HangarAircraft,
  HangarFlight,
  HangarLiveryOption,
} from "../types";
import { EMPTY, integer, parseGameDate, shortDate, shortMoney } from "../format";
import { hubLabel } from "../hubFlag";
import { splitLiveryName } from "../liveryName";
import { MenuSelect } from "./MenuSelect";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";

interface AircraftEditorProps {
  aircraftId: number;
  snapshot: CommandCenterSnapshot | null;
  onDataChanged: () => void;
  /** Back to the fleet browser. */
  onClose: () => void;
  /** Switch to another aircraft (the jump search and the recent list). */
  onOpenAircraft: (aircraftId: number) => void;
}

function exactMoney(value: number | null | undefined): string {
  return value === null || value === undefined ? EMPTY : `$${integer.format(value)}`;
}

type SeatKey = "eco" | "bus" | "first" | "payload";
type SeatMap = { eco: number; bus: number; first: number; payload: number };

// Tonnes per seat, straight from aircraft_buyer.py's offline capacity check
// (`0.1*eco + 0.125*bus + 0.15*first + cargo <= max_tonnage`, verified against
// the game's own purchase-time rejection). Cargo is already in tonnes, 1:1.
const SEAT_WEIGHT_T: Record<"eco" | "bus" | "first", number> = { eco: 0.1, bus: 0.125, first: 0.15 };
const SEAT_SPACE: Record<"eco" | "bus" | "first", number> = { eco: 1, bus: 1.8, first: 4.2 };

/** How high one field can go while the other three stay put — both the model's
 *  cabin-space cap and its weight cap must hold, same as the
 *  in-game reconfigure sliders. */
export function seatCeiling(key: SeatKey, seats: SeatMap, maxPax: number, maxTon: number): number {
  if (key === "payload") {
    // Cargo is whole tonnes to the game, so the headroom is floored: a 30.72 t
    // ceiling is 30 t you can actually submit.
    const usedTon = seats.eco * SEAT_WEIGHT_T.eco + seats.bus * SEAT_WEIGHT_T.bus + seats.first * SEAT_WEIGHT_T.first;
    return Math.max(0, Math.floor(maxTon - usedTon));
  }
  const others = (["eco", "bus", "first"] as const).filter((k) => k !== key);
  const usedSpace = others.reduce((sum, k) => sum + seats[k] * SEAT_SPACE[k], 0);
  const usedTon = others.reduce((sum, k) => sum + seats[k] * SEAT_WEIGHT_T[k], 0) + seats.payload;
  const byPax = (maxPax - usedSpace) / SEAT_SPACE[key];
  const byTon = (maxTon - usedTon) / SEAT_WEIGHT_T[key];
  return Math.max(0, Math.floor(Math.min(byPax, byTon)));
}

const SEAT_FIELDS = [
  { key: "eco", label: "Economy", tone: "is-eco", unit: "seats" },
  { key: "bus", label: "Business", tone: "is-bus", unit: "seats" },
  { key: "first", label: "First", tone: "is-first", unit: "seats" },
  { key: "payload", label: "Cargo", tone: "is-cargo", unit: "t" },
] as const;

/** The market only accepts useful prices in whole millions. */
function MillionField({ label, millions, onChange, min, max }: {
  label: string;
  millions: string;
  onChange: (value: string) => void;
  min?: number | null;
  max?: number | null;
}) {
  const minM = min ? Math.ceil(min / 1_000_000) : 1;
  const maxM = max ? Math.floor(max / 1_000_000) : 0;
  const value = Number(millions);
  const invalid = !!value && (value < minM || (!!maxM && value > maxM));
  return (
    <label className="hangar-sale-field">
      <span>{label}</span>
      <div className={`million-input${invalid ? " is-invalid" : ""}`}>
        <span className="million-input-currency">$</span>
        <Input
          className="million-input-value"
          type="number"
          min={minM}
          max={maxM || undefined}
          value={millions}
          onChange={(event) => onChange(event.target.value.replace(/\D/g, "").slice(0, 6))}
          inputMode="numeric"
        />
        <span className="million-input-suffix">million</span>
      </div>
      <small>
        Min ${integer.format(minM)}M{maxM ? ` · Max $${integer.format(maxM)}M` : ""}
        {!!maxM && value !== maxM && (
          <Button type="button" onClick={() => onChange(String(maxM))}>Use max</Button>
        )}
      </small>
    </label>
  );
}

export function salePricesValid(bin: number, start: number, minStart: number,
                                maxStart: number, maxBin: number): boolean {
  return bin >= start && start >= minStart && start <= maxStart && bin <= maxBin;
}

function clock(value: string | null) {
  const date = parseGameDate(value);
  return date ? date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }) : value || EMPTY;
}

/** A rotation slot routinely ends the next day, and a bare "09:00 – 08:59"
 *  reads as a flight that landed before it left. Mark the day roll. */
function slot(flight: HangarFlight) {
  const from = parseGameDate(flight.departure);
  const to = parseGameDate(flight.arrival);
  const rolls = from && to && from.toDateString() !== to.toDateString();
  return `${clock(flight.departure)} – ${clock(flight.arrival)}${rolls ? " +1d" : ""}`;
}

const DAY_LABELS = ["Today", "+1", "+2", "+3", "+4", "+5", "+6"];

// The last few aircraft opened, kept in this browser only: the hangar is a
// workbench and the plane being worked on is usually the one from a minute ago.
const JUMP_LIMIT = 8;
const RECENT_KEY = "hangar.recent";
const RECENT_LIMIT = 8;
interface Recent { aircraft_id: number; name: string; model: string; hub_iata: string | null; skin_id: number | null }
function readRecent(): Recent[] {
  try { return JSON.parse(window.localStorage.getItem(RECENT_KEY) || "[]"); } catch { return []; }
}
function writeRecent(items: Recent[]) {
  try { window.localStorage.setItem(RECENT_KEY, JSON.stringify(items)); } catch { /* private mode */ }
}

/** Every action is one in-game write, so each button reports its own state. */
type ActionKey = "name" | "seats" | "hub" | "livery" | "sell" | "scrap" | "unschedule";

// Actions that hand back a refreshed aircraft have no result to report, so the
// banner names the write instead. The rest phrase their own outcome.
const DONE_MESSAGE: Partial<Record<ActionKey, string>> = {
  name: "Renamed.",
  seats: "Seats reconfigured.",
  hub: "Hub changed.",
  livery: "Livery applied.",
};

export function AircraftEditor({ aircraftId, snapshot, onDataChanged, onClose, onOpenAircraft }: AircraftEditorProps) {
  const nameListId = useId();

  const [query, setQuery] = useState("");
  const [jumpOpen, setJumpOpen] = useState(false);
  const [candidates, setCandidates] = useState<FleetAircraft[]>([]);
  const [listLoading, setListLoading] = useState(false);
  const [recent, setRecent] = useState<Recent[]>(readRecent);

  const [aircraft, setAircraft] = useState<HangarAircraft | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<ActionKey | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const [name, setName] = useState("");
  const [nameSuggestions, setNameSuggestions] = useState<string[]>([]);
  const [targetHub, setTargetHub] = useState("");
  const [seats, setSeats] = useState({ eco: 0, bus: 0, first: 0, payload: 0 });
  const [liveries, setLiveries] = useState<HangarLiveryOption[] | null>(null);
  const [liveryBusy, setLiveryBusy] = useState(false);
  const [day, setDay] = useState(0);
  const [flights, setFlights] = useState<HangarFlight[] | null>(null);
  const [binMillions, setBinMillions] = useState("");
  const [startMillions, setStartMillions] = useState("");
  const [duration, setDuration] = useState("11");
  const [scrapConfirm, setScrapConfirm] = useState("");
  const [scrapOpen, setScrapOpen] = useState(false);

  const hubCountries = useMemo(() => new Map(
    (snapshot?.fleet_facets.hubs || []).map((hub) => [hub.hub_iata, hub.country_code]),
  ), [snapshot]);

  // Jump search reads the cached fleet table, so typing stays instant and no
  // game request is spent until an aircraft is actually opened.
  useEffect(() => {
    const term = query.trim();
    if (!term) { setCandidates([]); return; }
    let cancelled = false;
    setListLoading(true);
    const timer = window.setTimeout(async () => {
      try {
        const result = await fetchFleetPage({ q: term, limit: JUMP_LIMIT });
        if (!cancelled) setCandidates(result.items);
      } catch {
        if (!cancelled) setCandidates([]);
      } finally {
        if (!cancelled) setListLoading(false);
      }
    }, 220);
    return () => { cancelled = true; window.clearTimeout(timer); };
  }, [query]);

  useEffect(() => {
    let cancelled = false;
    const prefix = name.trim();
    if (prefix.length < 2 || prefix === aircraft?.name) {
      setNameSuggestions([]);
      return;
    }
    const timer = window.setTimeout(async () => {
      try {
        const matches = await fetchFleetNameSuggestions(prefix);
        if (!cancelled) setNameSuggestions(matches);
      } catch {
        if (!cancelled) setNameSuggestions([]);
      }
    }, 180);
    return () => { cancelled = true; window.clearTimeout(timer); };
  }, [name, aircraft?.name]);

  const applyAircraft = useCallback((next: HangarAircraft) => {
    setAircraft(next);
    setName(next.name);
    setTargetHub(next.hub_iata || "");
    setSeats({ eco: next.seats.eco, bus: next.seats.bus, first: next.seats.first, payload: next.payload });
    setBinMillions(next.sale.bin_threshold ? String(Math.floor(next.sale.bin_threshold / 1_000_000)) : "");
    setStartMillions(next.sale.max_start_bid ? String(Math.floor(next.sale.max_start_bid / 1_000_000)) : "");
    setScrapConfirm("");
    setScrapOpen(false);
    setRecent((prev) => {
      const entry = { aircraft_id: next.aircraft_id, name: next.name, model: next.model, hub_iata: next.hub_iata, skin_id: next.skin.id };
      const items = [entry, ...prev.filter((item) => item.aircraft_id !== next.aircraft_id)].slice(0, RECENT_LIMIT);
      writeRecent(items);
      return items;
    });
  }, []);

  const load = useCallback(async (aircraftId: number) => {
    setLoading(true);
    setError(null);
    setLiveries(null);
    setFlights(null);
    try {
      applyAircraft(await fetchHangarAircraft(aircraftId));
    } catch (reason) {
      setAircraft(null);
      setError(reason instanceof Error ? reason.message : "Could not read the aircraft");
    } finally {
      setLoading(false);
    }
  }, [applyAircraft]);

  useEffect(() => { load(aircraftId); }, [aircraftId, load]);

  const jump = (nextId: number) => {
    setQuery("");
    setJumpOpen(false);
    if (nextId !== aircraftId) { setError(null); setNotice(null); onOpenAircraft(nextId); }
  };

  // One wrapper for every write: it holds the busy flag, reports the failure
  // in place, and refreshes the cached fleet views the change invalidates.
  const run = async (key: ActionKey, action: () => Promise<HangarAircraft | string>) => {
    if (busy) return;
    setBusy(key);
    setError(null);
    setNotice(null);
    try {
      const result = await action();
      if (typeof result === "string") setNotice(result);
      else { applyAircraft(result); setNotice(DONE_MESSAGE[key] || "Done."); }
      onDataChanged();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Action failed");
    } finally {
      setBusy(null);
    }
  };

  const openLiveries = async () => {
    if (!aircraft || liveries) return;
    setLiveryBusy(true);
    try {
      setLiveries((await fetchHangarLiveries(aircraft.aircraft_id)).liveries);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Could not read the livery options");
    } finally {
      setLiveryBusy(false);
    }
  };

  const loadSchedule = useCallback(async (aircraftId: number, which: number) => {
    setFlights(null);
    try {
      setFlights((await fetchHangarSchedule(aircraftId, which)).flights);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Could not read the schedule");
      setFlights([]);
    }
  }, []);

  const paint = (skinId: number) => {
    if (!aircraft) return;
    const worn = (aircraft.skin.name || "").toLowerCase();
    const overwritesAward = !worn.includes("manufacturer livery");
    if (overwritesAward && !window.confirm(
      `${aircraft.name} wears “${aircraft.skin.name}”.\n\n`
      + "An awarded challenge or event livery cannot be re-applied once it is painted over. Repaint anyway?")) return;
    run("livery", async () => {
      const next = await paintHangarAircraft(aircraft.aircraft_id, skinId, overwritesAward);
      setLiveries(null);
      return next;
    });
  };

  const seatsChanged = aircraft
    && (seats.eco !== aircraft.seats.eco || seats.bus !== aircraft.seats.bus
      || seats.first !== aircraft.seats.first || seats.payload !== aircraft.payload);

  // ponytail: an unmodeled aircraft falls back to a loose ceiling — the game
  // still enforces the real one on submit either way. A pure freighter really
  // does report 0 seats, so the fallback has to test for null, not falsiness.
  const maxPax = aircraft?.model_seats_total ?? 500;
  const maxTon = aircraft?.model_payload_t ?? 200;
  const share = (part: number, whole: number) => (whole > 0 ? Math.min(100, (part / whole) * 100) : 0);
  const spaceUsed = (["eco", "bus", "first"] as const).reduce((sum, k) => sum + seats[k] * SEAT_SPACE[k], 0);
  const seatTon = (["eco", "bus", "first"] as const).reduce((sum, k) => sum + seats[k] * SEAT_WEIGHT_T[k], 0);
  const payloadTon = seatTon + seats.payload;
  const tonOf = (key: SeatKey) => (key === "payload" ? seats.payload : seats[key] * SEAT_WEIGHT_T[key]);
  const binPriceM = Number(binMillions);
  const startPriceM = Number(startMillions);
  const minStartM = Math.ceil((aircraft?.sale.min_start_bid || 0) / 1_000_000);
  const maxStartM = Math.floor((aircraft?.sale.max_start_bid || 0) / 1_000_000);
  const maxBinM = Math.floor((aircraft?.sale.bin_threshold || 0) / 1_000_000);
  const saleValid = salePricesValid(binPriceM, startPriceM, minStartM, maxStartM, maxBinM);

  /** The game's own sliders snap an over-cap value back to what still fits, so
   *  the field can be dragged past its ceiling but never left there. */
  const setSeat = (key: SeatKey, raw: number) => setSeats((prev) => ({
    ...prev,
    [key]: Math.max(0, Math.min(seatCeiling(key, prev, maxPax, maxTon), Math.floor(raw) || 0)),
  }));

  const thumb = (skinId: number | null) => (
    <span className="hangar-thumb">
      {skinId ? <img src={`/api/skin_image/${skinId}/medium`} alt="" loading="lazy" /> : <Plane size={16} />}
    </span>
  );

  // With nothing typed the menu offers the last few planes opened; once a
  // term is in, it lists the matches.
  const searching = query.trim() !== "";
  const jumpRows = searching ? candidates : recent.filter((item) => item.aircraft_id !== aircraftId);

  return (
    <div className="hangar-layout">
      {/* Above the panel on purpose: scrapping empties the workbench, and the
          confirmation of an irreversible action has to outlive it. */}
      {error && <div className="hangar-alert is-error"><AlertTriangle size={14} /><span>{error}</span></div>}
      {notice && !error && <div className="hangar-alert"><Check size={14} /><span>{notice}</span></div>}

      <section className="hangar-panel">
        <nav className="hangar-bar">
          <Button className="icon-action" onClick={onClose}>
            <ArrowLeft size={15} /> <span>All aircraft</span>
          </Button>
          <div className="hangar-jump">
            <label className="search-control">
              <Search size={15} />
              <Input
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                onFocus={() => setJumpOpen(true)}
                onBlur={() => setJumpOpen(false)}
                placeholder="Jump to another aircraft"
              />
              {query && <Button onClick={() => setQuery("")} aria-label="Clear search"><X size={14} /></Button>}
            </label>
            {jumpOpen && (searching || jumpRows.length > 0) && (
              <div className="hangar-jump-menu">
                {!searching && <p><History size={11} /> Recently opened</p>}
                {jumpRows.map((item) => (
                  // mousedown, not click: the input blurs first and would close the menu.
                  <Button key={item.aircraft_id} onMouseDown={(event) => { event.preventDefault(); jump(item.aircraft_id); }}>
                    {thumb(item.skin_id)}
                    <span className="hangar-copy">
                      <strong>{item.name}</strong>
                      <small>{item.model}{item.hub_iata ? ` · ${hubLabel(item.hub_iata, hubCountries.get(item.hub_iata))}` : ""}</small>
                    </span>
                  </Button>
                ))}
                {searching && jumpRows.length === 0 && <p>{listLoading ? "Searching…" : "No aircraft match."}</p>}
              </div>
            )}
          </div>
        </nav>

        {loading && <div className="fleet-loading">Reading the aircraft…</div>}

        {aircraft && (
          <>
            <header className="hangar-head">
              <span className="hangar-head-art">
                {aircraft.skin.id
                  ? <img src={`/api/skin_image/${aircraft.skin.id}`} alt="" />
                  : <Plane size={30} />}
              </span>
              <div className="hangar-head-copy">
                <p className="section-kicker"><b>{aircraft.icao_code || "ICAO ?"}</b> · {aircraft.model} · #{aircraft.aircraft_id}</p>
                <h2>{aircraft.name}</h2>
                <div className="hangar-head-chips">
                  <span className="hub-code">{aircraft.hub_iata ? hubLabel(aircraft.hub_iata, hubCountries.get(aircraft.hub_iata)) : EMPTY}</span>
                  <span className="livery-chip tone-special">{splitLiveryName(aircraft.skin.name).livery}</span>
                  <span className="haul-chip">{Math.round(aircraft.utilization)}% used</span>
                  <span className="haul-chip">wear {aircraft.wear.toFixed(1)}%</span>
                  <span className="haul-chip">{aircraft.mark || EMPTY}</span>
                  {aircraft.is_rental && <span className="haul-chip">rented</span>}
                </div>
              </div>
              <Button className="icon-action" onClick={() => load(aircraft.aircraft_id)} disabled={!!busy}>
                <RefreshCcw size={15} /> <span>Reload</span>
              </Button>
              <dl className="hangar-head-stats">
                <div><dt>Category</dt><dd>{aircraft.category ? `${aircraft.category} / 10` : EMPTY}</dd></div>
                <div><dt>Speed</dt><dd>{aircraft.speed_kmh ? `${integer.format(aircraft.speed_kmh)} km/h` : EMPTY}</dd></div>
                <div><dt>Range</dt><dd>{aircraft.range_km ? `${integer.format(aircraft.range_km)} km` : EMPTY}</dd></div>
                <div><dt>Capacity</dt><dd>{[
                  aircraft.max_pax ? `${integer.format(aircraft.max_pax)} pax` : "",
                  aircraft.max_tonnage ? `${integer.format(aircraft.max_tonnage)} t` : "",
                ].filter(Boolean).join(" · ") || EMPTY}</dd></div>
                <div><dt>Purchased</dt><dd>{shortDate(aircraft.purchased_at)}</dd></div>
                <div><dt>Purchase price</dt><dd>{exactMoney(aircraft.raw_price)}</dd></div>
                <div><dt>Catalogue</dt><dd>{exactMoney(aircraft.gross_price)}</dd></div>
              </dl>
            </header>

            <div className="hangar-cards">
              <article className="hangar-card">
                <h3><Tag size={15} /> Name</h3>
                <p>Renaming echoes the current seat map back, so it costs nothing.</p>
                <div className="hangar-row">
                  <Input className="model-input" value={name} onChange={(event) => setName(event.target.value)}
                    list={nameListId} maxLength={20} autoComplete="off" />
                  <datalist id={nameListId}>
                    {nameSuggestions.map((suggestion) => <option key={suggestion} value={suggestion} />)}
                  </datalist>
                  {aircraft.suggested_name && aircraft.suggested_name !== name && (
                    <Button type="button" className="neutral-action" onClick={() => setName(aircraft.suggested_name!)}
                      title={`Next in this livery's series: ${aircraft.suggested_name}`}>
                      {aircraft.suggested_name}
                    </Button>
                  )}
                  <Button
                    className="primary-action"
                    disabled={busy === "name" || !name.trim() || name === aircraft.name}
                    onClick={() => run("name", () => {
                      const trimmed = name.trim();
                      if (trimmed.length > 20) throw new Error("Name must be 20 characters or fewer");
                      return renameHangarAircraft(aircraft.aircraft_id, trimmed);
                    })}
                  >
                    {busy === "name" ? "Renaming…" : "Rename"}
                  </Button>
                </div>
              </article>

              <article className="hangar-card">
                <h3><Building2 size={15} /> Hub</h3>
                <p>Relocating an aircraft is a paid in-game transfer.</p>
                <div className="hangar-row">
                  <MenuSelect
                    label="Move to"
                    value={targetHub}
                    onChange={setTargetHub}
                    options={aircraft.hubs.map((hub) => ({
                      value: hub.hub_iata,
                      label: hubLabel(hub.hub_iata, hubCountries.get(hub.hub_iata)),
                    }))}
                    icon={Building2}
                  />
                  <Button
                    className="primary-action"
                    disabled={busy === "hub" || !targetHub || targetHub === aircraft.hub_iata}
                    onClick={() => run("hub", () => moveHangarAircraft(aircraft.aircraft_id, targetHub))}
                  >
                    {busy === "hub" ? "Moving…" : "Move"}
                  </Button>
                </div>
              </article>

              <article className="hangar-card is-wide">
                <h3><Palette size={15} /> Livery</h3>
                <p>
                  Wearing <b>{aircraft.skin.name || EMPTY}</b>. A livery raises the market ceiling
                  ({shortMoney(aircraft.sale.bin_threshold)} today) and is free once owned.
                </p>
                {!liveries && (
                  <Button className="primary-action" onClick={openLiveries} disabled={liveryBusy}>
                    {liveryBusy ? "Loading liveries…" : "Change livery"}
                  </Button>
                )}
                {liveries && (
                  <div className="hangar-livery-grid">
                    {liveries.map((livery) => (
                      <Button
                        key={livery.skin_id}
                        className={`hangar-livery${livery.is_current ? " is-current" : ""}`}
                        disabled={livery.is_current || busy === "livery"}
                        onClick={() => paint(livery.skin_id)}
                      >
                        <span className="livery-image">
                          <img src={`/api/skin_image/${livery.skin_id}/medium`} alt="" loading="lazy" />
                          {livery.is_current && <span className="livery-badge is-owned">Worn</span>}
                        </span>
                        <strong>{splitLiveryName(livery.name).livery}</strong>
                        <small>{livery.purchased ? "Owned" : livery.price_amcoins ? `${livery.price_amcoins} AM coins` : "Free"}</small>
                      </Button>
                    ))}
                    {liveries.length === 0 && <p className="hangar-empty">No livery can be applied to this model.</p>}
                  </div>
                )}
              </article>

              <article className="hangar-card is-wide">
                <h3><Layers size={15} /> Configuration</h3>
                <p>
                  Reconfiguring is paid. Seats and cargo share one weight budget, so a slider
                  dragged past what is left snaps back to what still fits.
                </p>

                <div className="cap-bars">
                  <div className="cap-bar">
                    <div className="cap-bar-head">
                      <span>Cabin used</span>
                      <strong>
                        {integer.format(spaceUsed)} / {integer.format(maxPax)}
                        <em>{Math.round(share(spaceUsed, maxPax))}%</em>
                      </strong>
                    </div>
                    <div className="cap-track">
                      {SEAT_FIELDS.filter((f) => f.key !== "payload").map((field) => (
                        <i key={field.key} className={field.tone} style={{ width: `${share(seats[field.key] * SEAT_SPACE[field.key], maxPax)}%` }} />
                      ))}
                    </div>
                    <div className="cap-keys">
                      {SEAT_FIELDS.filter((f) => f.key !== "payload").map((field) => (
                        <span key={field.key} className={`cap-key ${field.tone}`}>
                          {field.label} <b>{integer.format(seats[field.key])}</b>
                        </span>
                      ))}
                    </div>
                  </div>

                  <div className="cap-bar">
                    <div className="cap-bar-head">
                      <span>Total payload</span>
                      <strong>
                        {payloadTon.toFixed(2)} / {maxTon.toFixed(2)} t
                        <em>{Math.round(share(payloadTon, maxTon))}%</em>
                      </strong>
                    </div>
                    <div className="cap-track">
                      {SEAT_FIELDS.map((field) => (
                        <i key={field.key} className={field.tone} style={{ width: `${share(tonOf(field.key), maxTon)}%` }} />
                      ))}
                    </div>
                    <div className="cap-keys">
                      <span className="cap-key is-seats">Seat weight <b>{seatTon.toFixed(2)} t</b></span>
                      <span className="cap-key is-cargo">Cargo <b>{seats.payload.toFixed(2)} t</b></span>
                    </div>
                  </div>
                </div>

                <div className="hangar-seats">
                  {SEAT_FIELDS.map(({ key, label, tone, unit }) => {
                    const ceiling = seatCeiling(key, seats, maxPax, maxTon);
                    const scale = key === "payload" ? maxTon : Math.floor(maxPax / SEAT_SPACE[key]);
                    const value = seats[key];
                    return (
                      <div className={`hangar-seat-row ${tone}`} key={key}>
                        <div className="hangar-seat-row-head">
                          <span>
                            {label}
                            <small className="hangar-seat-ceiling">up to {ceiling} {unit}</small>
                          </span>
                          <Input
                            className="model-input hangar-seat-value"
                            type="number"
                            min={0}
                            max={ceiling}
                            value={value}
                            onChange={(event) => setSeat(key, Number(event.target.value))}
                          />
                        </div>
                        {/* Ranged to the model's own capacity, not to the ceiling: the
                            grey headroom past the fill is what the other three fields
                            have already spent, which a rescaled track would hide. */}
                        <Input
                          className="hangar-seat-slider"
                          type="range"
                          min={0}
                          max={scale}
                          value={value}
                          style={{
                            "--pct": `${share(value, scale)}%`,
                            "--ceil": `${share(ceiling, scale)}%`,
                          } as React.CSSProperties}
                          onChange={(event) => setSeat(key, Number(event.target.value))}
                        />
                      </div>
                    );
                  })}
                </div>
                <Button
                  className="primary-action"
                  disabled={busy === "seats" || !seatsChanged}
                  onClick={() => run("seats", () => reconfigureHangarAircraft(aircraft.aircraft_id, seats))}
                >
                  <Wrench size={14} /> {busy === "seats" ? "Reconfiguring…" : "Reconfigure"}
                </Button>
              </article>

              <article className="hangar-card is-wide">
                <h3><CalendarDays size={15} /> Schedule</h3>
                <p>Flights are read from the mobile API; clearing them still goes through Chrome.</p>
                <div className="hangar-days">
                  {DAY_LABELS.map((label, index) => (
                    <Button
                      key={label}
                      className={`segmented-option${index === day && flights ? " is-active" : ""}`}
                      onClick={() => { setDay(index); loadSchedule(aircraft.aircraft_id, index); }}
                    >
                      {label}
                    </Button>
                  ))}
                  <Button
                    className="danger-action is-soft"
                    disabled={busy === "unschedule"}
                    onClick={() => {
                      if (!window.confirm(`Clear every scheduled flight for ${aircraft.name}?`)) return;
                      run("unschedule", async () => {
                        await unscheduleHangarAircraft(aircraft.aircraft_id);
                        setFlights([]);
                        return "Schedule cleared.";
                      });
                    }}
                  >
                    {busy === "unschedule" ? "Clearing…" : "Clear schedule"}
                  </Button>
                </div>
                {flights === null
                  ? <p className="hangar-empty">Pick a day to read its flights.</p>
                  : flights.length === 0
                    ? <p className="hangar-empty">No flights scheduled that day.</p>
                    : (
                      <ul className="hangar-flights">
                        {flights.map((flight) => (
                          <li key={flight.flight_id}>
                            <strong>{flight.from_iata} → {flight.to_iata}</strong>
                            <small>{slot(flight)}</small>
                          </li>
                        ))}
                      </ul>
                    )}
              </article>

              <article className="hangar-card is-wide">
                <h3><Gavel size={15} /> Sell on the market</h3>
                <p>Buy now must stay between the opening bid and the livery ceiling. Listings cannot be cancelled.</p>
                <div className="hangar-sale-summary">
                  <MillionField
                    label="Buy now"
                    millions={binMillions}
                    onChange={setBinMillions}
                    min={startPriceM * 1_000_000}
                    max={aircraft.sale.bin_threshold}
                  />
                  <MillionField
                    label="Opening bid"
                    millions={startMillions}
                    onChange={setStartMillions}
                    min={aircraft.sale.min_start_bid}
                    max={aircraft.sale.max_start_bid}
                  />
                  <label className="hangar-sale-field">
                    <span>Duration</span>
                    <div className="million-input">
                      <Input
                        className="million-input-value"
                        type="number"
                        min={1}
                        max={48}
                        value={duration}
                        onChange={(event) => setDuration(event.target.value.replace(/\D/g, "").slice(0, 2))}
                        inputMode="numeric"
                      />
                      <span className="million-input-suffix">hours</span>
                    </div>
                    <small>Min 1 hour · Max 48 hours</small>
                  </label>
                  <Button
                    className="primary-action"
                    disabled={busy === "sell" || !saleValid}
                    onClick={() => run("sell", async () => {
                      const auction = await listHangarAircraft(aircraft.aircraft_id, {
                        bin_price: Number(binMillions) * 1_000_000,
                        price: Number(startMillions) ? Number(startMillions) * 1_000_000 : undefined,
                        duration: Number(duration) || 11,
                      });
                      return `Listed as auction ${auction.auction_id} at ${shortMoney(auction.bin_price)}.`;
                    })}
                  >
                    {busy === "sell" ? "Listing…" : `List for ${shortMoney(binPriceM * 1_000_000)}`}
                  </Button>
                </div>
              </article>

              <article className="hangar-card is-wide is-danger">
                <h3><Flame size={15} /> Sell for scrap</h3>
                <p>
                  The game pays {shortMoney(aircraft.sale.scrap)} and the aircraft is gone for good — the
                  market almost always pays more.
                </p>
                <div className="hangar-row">
                  <Button
                    className="danger-action"
                    onClick={() => { setScrapConfirm(""); setScrapOpen(true); }}
                  >
                    Scrap aircraft
                  </Button>
                </div>
              </article>
            </div>

            {scrapOpen && (
              <div className="modal-overlay" onClick={() => busy !== "scrap" && setScrapOpen(false)}>
                <form
                  className="confirm-modal"
                  role="dialog"
                  aria-modal="true"
                  aria-label={`Scrap ${aircraft.name}`}
                  onClick={(event) => event.stopPropagation()}
                  onSubmit={(event) => {
                    event.preventDefault();
                    run("scrap", async () => {
                      const result = await scrapHangarAircraft(aircraft.aircraft_id, scrapConfirm.trim());
                      setScrapOpen(false);
                      setRecent((prev) => {
                        const items = prev.filter((item) => item.aircraft_id !== aircraft.aircraft_id);
                        writeRecent(items);
                        return items;
                      });
                      onClose();
                      return `${result.name} scrapped for ${shortMoney(result.scrapped_for)}.`;
                    });
                  }}
                >
                  <h3>Scrap this aircraft?</h3>
                  <p>
                    Scrapping is irreversible. {aircraft.name} leaves the fleet for good, its schedule
                    goes with it, and the game pays {shortMoney(aircraft.sale.scrap)}.
                  </p>
                  <p className="confirm-modal-ask">
                    To confirm, type <b>{aircraft.name}</b> in the box below:
                  </p>
                  <div className="confirm-modal-input">
                    <Input
                      value={scrapConfirm}
                      onChange={(event) => setScrapConfirm(event.target.value)}
                      placeholder={aircraft.name}
                      autoFocus
                      autoComplete="off"
                      spellCheck={false}
                    />
                    <Button type="button" onClick={() => setScrapConfirm(aircraft.name)}>Autofill</Button>
                  </div>
                  <div className="confirm-modal-actions">
                    <Button type="button" className="neutral-action" onClick={() => setScrapOpen(false)}>
                      Cancel
                    </Button>
                    <Button
                      type="submit"
                      className="danger-action is-solid"
                      disabled={busy === "scrap" || scrapConfirm.trim() !== aircraft.name}
                    >
                      {busy === "scrap" ? "Scrapping…" : "Confirm"}
                    </Button>
                  </div>
                </form>
              </div>
            )}
          </>
        )}
      </section>
    </div>
  );
}
