import React, { useCallback, useEffect, useId, useMemo, useState } from "react";
import {
  AlertTriangle,
  Building2,
  CalendarDays,
  Check,
  Flame,
  Gavel,
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
import { hubLabel } from "../hubFlag";
import { splitLiveryName } from "../liveryName";
import { MenuOption, MenuSelect } from "./MenuSelect";

interface HangarProps {
  snapshot: CommandCenterSnapshot | null;
  initialPreset?: string;
  refreshToken: number;
  onDataChanged: () => void;
}

const integer = new Intl.NumberFormat(undefined, { maximumFractionDigits: 0 });
const EMPTY = "—";

function money(value: number | null | undefined): string {
  if (value === null || value === undefined) return EMPTY;
  if (value >= 1_000_000_000) return `$${(value / 1_000_000_000).toFixed(2)}B`;
  if (value >= 1_000_000) return `$${(value / 1_000_000).toFixed(0)}M`;
  return `$${integer.format(value)}`;
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
        <input
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
          <button type="button" onClick={() => onChange(String(maxM))}>Use max</button>
        )}
      </small>
    </label>
  );
}

export function salePricesValid(bin: number, start: number, minStart: number,
                                maxStart: number, maxBin: number): boolean {
  return bin >= start && start >= minStart && start <= maxStart && bin <= maxBin;
}

function parseGameDate(value: string | null) {
  if (!value) return null;
  const date = new Date(`${value.replace(" ", "T").replace(/\.\d+$/, "")}Z`);
  return Number.isNaN(date.valueOf()) ? null : date;
}

function clock(value: string | null) {
  const date = parseGameDate(value);
  return date ? date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }) : value || EMPTY;
}

function shortDate(value: string | null) {
  const date = parseGameDate(value);
  return date ? date.toLocaleDateString([], { dateStyle: "medium" }) : EMPTY;
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

// The picker finds one aircraft; it is not a second fleet browser, so it reads
// a page rather than the whole hub. Whenever that page is short of the match
// count the list says so — a silent cap reads as "this hub has 60 aircraft".
const PICKER_LIMIT = 60;

/** Every action is one in-game write, so each button reports its own state. */
type ActionKey = "name" | "seats" | "hub" | "livery" | "sell" | "scrap" | "unschedule";

export function Hangar({ snapshot, initialPreset, refreshToken, onDataChanged }: HangarProps) {
  const presetId = initialPreset?.startsWith("ac:") ? Number(initialPreset.slice(3)) : undefined;
  const nameListId = useId();

  const [query, setQuery] = useState("");
  const [hubFilter, setHubFilter] = useState("all");
  const [candidates, setCandidates] = useState<FleetAircraft[]>([]);
  const [matched, setMatched] = useState(0);
  const [listError, setListError] = useState<string | null>(null);
  const [selectedId, setSelectedId] = useState<number | undefined>(presetId);

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

  const hubCountries = useMemo(() => new Map(
    (snapshot?.fleet_facets.hubs || []).map((hub) => [hub.hub_iata, hub.country_code]),
  ), [snapshot]);

  const hubOptions = useMemo<MenuOption[]>(() => [
    { value: "all", label: "All hubs" },
    ...(snapshot?.fleet_facets.hubs || []).map((hub) => ({
      value: hub.hub_iata,
      label: hubLabel(hub.hub_iata, hub.country_code),
      hint: `${hub.count} aircraft`,
    })),
  ], [snapshot]);

  // Candidate list: the cached fleet table, so typing stays instant and no
  // game request is spent until an aircraft is actually opened.
  useEffect(() => {
    let cancelled = false;
    const timer = window.setTimeout(async () => {
      try {
        const page = await fetchFleetPage({ q: query.trim(), hub: hubFilter, limit: PICKER_LIMIT });
        if (!cancelled) { setCandidates(page.items); setMatched(page.total); setListError(null); }
      } catch (reason) {
        if (!cancelled) setListError(reason instanceof Error ? reason.message : "Fleet lookup failed");
      }
    }, query ? 220 : 0);
    return () => { cancelled = true; window.clearTimeout(timer); };
  }, [query, hubFilter, refreshToken]);

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

  useEffect(() => { if (selectedId) load(selectedId); }, [selectedId, load]);

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
      else { applyAircraft(result); setNotice("Done."); }
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

  return (
    <div className="hangar-layout">
      <section className="flat-section hangar-picker">
        <div className="hangar-picker-controls">
          <div className="search-control">
            <Search size={15} />
            <input
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="Find an aircraft by name or model"
            />
            {query && <button onClick={() => setQuery("")} aria-label="Clear search"><X size={14} /></button>}
          </div>
          <MenuSelect label="Hub" value={hubFilter} onChange={setHubFilter} options={hubOptions} icon={Building2} />
        </div>

        <div className="hangar-picker-list">
          {listError && <p className="hangar-empty">{listError}</p>}
          {!listError && candidates.length === 0 && <p className="hangar-empty">No aircraft match that search.</p>}
          {candidates.map((item) => (
            <button
              key={item.aircraft_id}
              className={`hangar-picker-row${item.aircraft_id === selectedId ? " is-active" : ""}`}
              onClick={() => setSelectedId(item.aircraft_id)}
            >
              <span className="hangar-picker-art">
                {item.skin_id
                  ? <img src={`/api/skin_image/${item.skin_id}/medium`} alt="" loading="lazy" />
                  : <Plane size={16} />}
              </span>
              <span className="hangar-picker-copy">
                <strong>{item.name}</strong>
                <small>{item.model} · {hubLabel(item.hub_iata, hubCountries.get(item.hub_iata))} · {Math.round(item.utilization)}%</small>
              </span>
            </button>
          ))}
          {candidates.length > 0 && matched > candidates.length && (
            <p className="hangar-picker-foot">
              Showing {candidates.length} of {integer.format(matched)} — search or pick a hub to narrow it down.
            </p>
          )}
        </div>
      </section>

      <section className="hangar-panel">
        {/* Outside the panel below on purpose: scrapping empties the workbench,
            and the confirmation of an irreversible action has to outlive it. */}
        {error && <div className="hangar-alert is-error"><AlertTriangle size={14} /><span>{error}</span></div>}
        {notice && !error && <div className="hangar-alert"><Check size={14} /><span>{notice}</span></div>}

        {selectedId && loading && <div className="fleet-loading">Reading the aircraft…</div>}

        {!aircraft && !loading && (
          <div className="table-empty">
            <strong>Pick an aircraft</strong>
            <span>Everything here writes straight to the game through the mobile API.</span>
          </div>
        )}

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
                  <span className="livery-chip tone-special">{splitLiveryName(aircraft.skin.name || "").livery}</span>
                  <span className="haul-chip">{Math.round(aircraft.utilization)}% used</span>
                  <span className="haul-chip">wear {aircraft.wear.toFixed(1)}%</span>
                  <span className="haul-chip">{aircraft.mark || EMPTY}</span>
                  {aircraft.is_rental && <span className="haul-chip">rented</span>}
                </div>
              </div>
              <button className="icon-action" onClick={() => load(aircraft.aircraft_id)} disabled={!!busy}>
                <RefreshCcw size={15} /> <span>Reload</span>
              </button>
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
                  <input className="model-input" value={name} onChange={(event) => setName(event.target.value)}
                    list={nameListId} maxLength={20} autoComplete="off" />
                  <datalist id={nameListId}>
                    {nameSuggestions.map((suggestion) => <option key={suggestion} value={suggestion} />)}
                  </datalist>
                  <button
                    className="primary-action"
                    disabled={busy === "name" || !name.trim() || name === aircraft.name}
                    onClick={() => run("name", () => {
                      const trimmed = name.trim();
                      if (trimmed.length > 20) throw new Error("Name must be 20 characters or fewer");
                      return renameHangarAircraft(aircraft.aircraft_id, trimmed);
                    })}
                  >
                    {busy === "name" ? "Renaming…" : "Rename"}
                  </button>
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
                  <button
                    className="primary-action"
                    disabled={busy === "hub" || !targetHub || targetHub === aircraft.hub_iata}
                    onClick={() => run("hub", () => moveHangarAircraft(aircraft.aircraft_id, targetHub))}
                  >
                    {busy === "hub" ? "Moving…" : "Move"}
                  </button>
                </div>
              </article>

              <article className="hangar-card is-wide">
                <h3><Palette size={15} /> Livery</h3>
                <p>
                  Wearing <b>{aircraft.skin.name || EMPTY}</b>. A livery raises the market ceiling
                  ({money(aircraft.sale.bin_threshold)} today) and is free once owned.
                </p>
                {!liveries && (
                  <button className="primary-action" onClick={openLiveries} disabled={liveryBusy}>
                    {liveryBusy ? "Loading liveries…" : "Change livery"}
                  </button>
                )}
                {liveries && (
                  <div className="hangar-livery-grid">
                    {liveries.map((livery) => (
                      <button
                        key={livery.skin_id}
                        className={`hangar-livery${livery.is_current ? " is-current" : ""}`}
                        disabled={livery.is_current || busy === "livery"}
                        onClick={() => paint(livery.skin_id)}
                      >
                        <span className="livery-image">
                          <img src={`/api/skin_image/${livery.skin_id}/medium`} alt="" loading="lazy" />
                          {livery.is_current && <span className="livery-badge is-owned">Worn</span>}
                        </span>
                        <strong>{splitLiveryName(livery.name || "").livery}</strong>
                        <small>{livery.purchased ? "Owned" : livery.price_amcoins ? `${livery.price_amcoins} AM coins` : "Free"}</small>
                      </button>
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
                          <input
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
                        <input
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
                <button
                  className="primary-action"
                  disabled={busy === "seats" || !seatsChanged}
                  onClick={() => run("seats", () => reconfigureHangarAircraft(aircraft.aircraft_id, seats))}
                >
                  <Wrench size={14} /> {busy === "seats" ? "Reconfiguring…" : "Reconfigure"}
                </button>
              </article>

              <article className="hangar-card is-wide">
                <h3><CalendarDays size={15} /> Schedule</h3>
                <p>Flights are read from the mobile API; clearing them still goes through Chrome.</p>
                <div className="hangar-days">
                  {DAY_LABELS.map((label, index) => (
                    <button
                      key={label}
                      className={`segmented-option${index === day && flights ? " is-active" : ""}`}
                      onClick={() => { setDay(index); loadSchedule(aircraft.aircraft_id, index); }}
                    >
                      {label}
                    </button>
                  ))}
                  <button
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
                  </button>
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
                      <input
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
                  <button
                    className="primary-action"
                    disabled={busy === "sell" || !saleValid}
                    onClick={() => run("sell", async () => {
                      const auction = await listHangarAircraft(aircraft.aircraft_id, {
                        bin_price: Number(binMillions) * 1_000_000,
                        price: Number(startMillions) ? Number(startMillions) * 1_000_000 : undefined,
                        duration: Number(duration) || 11,
                      });
                      return `Listed as auction ${auction.auction_id} at ${money(auction.bin_price)}.`;
                    })}
                  >
                    {busy === "sell" ? "Listing…" : `List for ${money(binPriceM * 1_000_000)}`}
                  </button>
                </div>
              </article>

              <article className="hangar-card is-wide is-danger">
                <h3><Flame size={15} /> Sell for scrap</h3>
                <p>
                  The game pays {money(aircraft.sale.scrap)} and the aircraft is gone for good — the
                  market almost always pays more. Click{" "}
                  <button type="button" className="text-link" onClick={() => setScrapConfirm(aircraft.name)}>
                    {aircraft.name}
                  </button>{" "}
                  or type it below to confirm.
                </p>
                <div className="hangar-row">
                  <input
                    className="model-input"
                    value={scrapConfirm}
                    onChange={(event) => setScrapConfirm(event.target.value)}
                    onKeyDown={(event) => {
                      if (event.key === "Tab" && aircraft.name.startsWith(scrapConfirm)) {
                        event.preventDefault();
                        setScrapConfirm(aircraft.name);
                      }
                    }}
                    placeholder={aircraft.name}
                  />
                  <button
                    className="danger-action"
                    disabled={busy === "scrap" || scrapConfirm.trim() !== aircraft.name}
                    onClick={() => run("scrap", async () => {
                      const result = await scrapHangarAircraft(aircraft.aircraft_id, scrapConfirm.trim());
                      setAircraft(null);
                      setSelectedId(undefined);
                      return `${result.name} scrapped for ${money(result.scrapped_for)}.`;
                    })}
                  >
                    {busy === "scrap" ? "Scrapping…" : "Scrap aircraft"}
                  </button>
                </div>
              </article>
            </div>
          </>
        )}
      </section>
    </div>
  );
}
