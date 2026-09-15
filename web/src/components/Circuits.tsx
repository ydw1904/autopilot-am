import { useEffect, useMemo, useState } from "react";
import { ArrowDownAZ, ArrowDownWideNarrow, ArrowLeft, ArrowUpAZ, ArrowUpNarrowWide, Building2, Check, Clock, Filter, Plane, Route, SlidersHorizontal, Waves } from "lucide-react";
import { fetchNetwork, fetchPricing } from "../api";
import { ClassValues as ClassRecord, NetworkCircuit } from "../types";
import { FleetBrowser } from "./FleetBrowser";
import { PRICE_CLASSES, dailyCapacity } from "../classes";
import { compactMoney, integer } from "../format";
import { hubLabel } from "../hubFlag";
import { FilterBar, SearchInput } from "./FilterBar";
import { MenuSelect } from "./MenuSelect";
import { EmptyState, ErrorState, LoadingState } from "./PageStates";
import { useApi } from "../useApi";
import { SectionHeader } from "./SectionHeader";
import { SegmentedControl } from "./SegmentedControl";
import { Button } from "@/components/ui/button";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";

interface CircuitsProps { refreshToken: number; onOpenAircraft: (aircraftId: number) => void }

const STATUS_LABEL: Record<string, string> = { planned: "Planned", bought: "Acquiring", completed: "Operating" };
const STATUS_TABS = [{ key: "all", label: "All circuits" }, { key: "completed", label: "Operating" }, { key: "bought", label: "Acquiring" }, { key: "planned", label: "Planned" }];
const ATTENTION_OPTIONS = [
  { value: "all", label: "Everything" },
  { value: "gaps", label: "Unscheduled waves", hint: "bought, not flying" },
  { value: "idle", label: "Idle aircraft", hint: "0% utilization" },
  { value: "routes", label: "Missing routes", hint: "not yet bought" },
  { value: "healthy", label: "Healthy only", hint: "none of the above" },
];
const SORT_GROUPS = [
  { label: "Revenue", options: [
    { value: "weekly_desc", label: "Weekly revenue", hint: "Highest first", icon: ArrowDownWideNarrow },
    { value: "weekly_asc", label: "Weekly revenue", hint: "Lowest first", icon: ArrowUpNarrowWide },
  ] },
  { label: "Name", options: [
    { value: "name_asc", label: "Circuit", hint: "A to Z", icon: ArrowDownAZ },
    { value: "name_desc", label: "Circuit", hint: "Z to A", icon: ArrowUpAZ },
  ] },
  { label: "Size", options: [
    { value: "routes_desc", label: "Routes", hint: "Most first", icon: Route },
    { value: "aircraft_desc", label: "Aircraft", hint: "Most first", icon: Plane },
    { value: "hours_desc", label: "Round trip", hint: "Longest first", icon: Clock },
  ] },
  { label: "Health", options: [
    { value: "util_asc", label: "Utilization", hint: "Idlest first", icon: ArrowUpNarrowWide },
    { value: "util_desc", label: "Utilization", hint: "Busiest first", icon: ArrowDownWideNarrow },
    { value: "gaps_desc", label: "Unscheduled waves", hint: "Most first", icon: Waves },
  ] },
];

export interface CircuitFilters { query: string; hub: string; status: string; model: string; attention: string }
const NO_FILTERS: CircuitFilters = { query: "", hub: "all", status: "all", model: "all", attention: "all" };

export function matchesCircuit(circuit: NetworkCircuit, filters: CircuitFilters): boolean {
  const term = filters.query.trim().toLowerCase();
  const gaps = Math.max(0, circuit.waves_bought - circuit.waves_scheduled) > 0;
  const idle = circuit.idle_aircraft > 0;
  const missing = circuit.routes_owned < circuit.routes.length;
  const attention = { all: true, gaps, idle, routes: missing, healthy: !gaps && !idle && !missing }[filters.attention] ?? true;
  return attention &&
    (filters.hub === "all" || circuit.hub_iata === filters.hub) &&
    (filters.status === "all" || circuit.status === filters.status) &&
    (filters.model === "all" || circuit.aircraft_model === filters.model) &&
    (!term || `${circuit.name} ${circuit.hub_iata} ${circuit.aircraft_model} ${circuit.routes.map((route) => route.dest_iata).join(" ")}`.toLowerCase().includes(term));
}

export function compareCircuits(sort: string) {
  const byName = (a: NetworkCircuit, b: NetworkCircuit) => a.name.localeCompare(b.name);
  const gaps = (c: NetworkCircuit) => Math.max(0, c.waves_bought - c.waves_scheduled);
  return (a: NetworkCircuit, b: NetworkCircuit): number => {
    switch (sort) {
      case "weekly_asc": return a.weekly_rev - b.weekly_rev || byName(a, b);
      case "name_asc": return byName(a, b);
      case "name_desc": return byName(b, a);
      case "routes_desc": return b.routes.length - a.routes.length || byName(a, b);
      case "aircraft_desc": return b.aircraft - a.aircraft || byName(a, b);
      case "hours_desc": return (b.total_hours || 0) - (a.total_hours || 0) || byName(a, b);
      case "util_asc": return a.avg_utilization - b.avg_utilization || byName(a, b);
      case "util_desc": return b.avg_utilization - a.avg_utilization || byName(a, b);
      case "gaps_desc": return gaps(b) - gaps(a) || byName(a, b);
      default: return b.weekly_rev - a.weekly_rev || byName(a, b);
    }
  };
}

/** Daily revenue per class = price × volume (carried for actual, demand for max). */
export function classRevenue(price: Partial<ClassRecord>, volume: Partial<ClassRecord>): number[] {
  return PRICE_CLASSES.map((key) => (price[key] ?? 0) * (volume[key] ?? 0));
}

/** The badges a circuit earns for what is wrong with it. Empty means healthy. */
export function circuitIssues(circuit: NetworkCircuit): string[] {
  const gaps = Math.max(0, circuit.waves_bought - circuit.waves_scheduled);
  const missing = circuit.routes.length - circuit.routes_owned;
  const plural = (count: number, noun: string) => `${count} ${noun}${count === 1 ? "" : "s"}`;
  return [
    gaps ? plural(gaps, "unscheduled wave") : "",
    circuit.idle_aircraft ? `${circuit.idle_aircraft} idle aircraft` : "",
    missing ? `${plural(missing, "route")} to buy` : "",
  ].filter(Boolean);
}

interface HubTile { hub: string; circuits: number; aircraft: number; weekly: number; issues: number }

/** One tile per hub that still has circuits after the non-hub filters. */
export function hubBoard(circuits: NetworkCircuit[]): HubTile[] {
  const tiles = new Map<string, HubTile>();
  for (const circuit of circuits) {
    const tile = tiles.get(circuit.hub_iata) ?? { hub: circuit.hub_iata, circuits: 0, aircraft: 0, weekly: 0, issues: 0 };
    tile.circuits += 1;
    tile.aircraft += circuit.aircraft;
    tile.weekly += circuit.weekly_rev;
    tile.issues += circuit.status !== "planned" && circuitIssues(circuit).length ? 1 : 0;
    tiles.set(circuit.hub_iata, tile);
  }
  return [...tiles.values()].sort((a, b) => b.weekly - a.weekly || a.hub.localeCompare(b.hub));
}

export function Circuits({ refreshToken, onOpenAircraft }: CircuitsProps) {
  const { data: snapshot, error } = useApi(fetchNetwork, [refreshToken]);
  const [selected, setSelected] = useState<string | null>(null);
  const [filters, setFilters] = useState<CircuitFilters>(NO_FILTERS);
  const [sort, setSort] = useState("weekly_desc");
  const patch = (next: Partial<CircuitFilters>) => setFilters((current) => ({ ...current, ...next }));

  // Every filter except the hub one: the board is built from this, so a tile
  // counts what clicking it would actually show under the other filters.
  const unhubbed = useMemo(() => (snapshot?.circuits ?? []).filter((circuit) => matchesCircuit(circuit, { ...filters, hub: "all" })), [snapshot, filters]);
  const circuits = useMemo(() => unhubbed.filter((circuit) => filters.hub === "all" || circuit.hub_iata === filters.hub).sort(compareCircuits(sort)), [unhubbed, filters.hub, sort]);

  if (error) return <ErrorState title="Circuits unavailable" message={error} />;
  if (!snapshot) return <LoadingState />;
  const activeCircuit = snapshot.circuits.find((circuit) => circuit.name === selected);
  if (activeCircuit) return <CircuitDetail circuit={activeCircuit} refreshToken={refreshToken} onBack={() => setSelected(null)} onOpenAircraft={onOpenAircraft} />;

  const { totals } = snapshot;
  const country = new Map(snapshot.hubs.map((item) => [item.hub_iata, item.country_code]));
  const models = [{ value: "all", label: "All aircraft" }, ...[...new Set(snapshot.circuits.map((circuit) => circuit.aircraft_model))].sort().map((item) => ({ value: item, label: item }))];
  const statusCounts = (key: string) => snapshot.circuits.filter((circuit) => matchesCircuit(circuit, { ...filters, status: key })).length;
  const activeFilters = Number(Boolean(filters.query.trim())) + Number(filters.hub !== "all") + Number(filters.status !== "all") + Number(filters.model !== "all") + Number(filters.attention !== "all");
  const fleetTotal = snapshot.circuits.reduce((sum, circuit) => sum + circuit.aircraft, 0);
  const routesPlanned = snapshot.circuits.reduce((sum, circuit) => sum + circuit.routes.length - circuit.routes_owned, 0);
  const board = hubBoard(unhubbed);
  const boardTotal = { weekly: board.reduce((sum, tile) => sum + tile.weekly, 0), aircraft: board.reduce((sum, tile) => sum + tile.aircraft, 0), issues: board.reduce((sum, tile) => sum + tile.issues, 0) };
  const topHub = Math.max(1, ...board.map((tile) => tile.weekly));

  return <div className="stack-workspace circuits-workspace">
    <section className="portfolio-strip" aria-label="Circuit summary">
      <div className="portfolio-lead"><span className="summary-label">All circuit revenue</span><strong>{compactMoney.format(totals.operating_weekly_rev + totals.planned_weekly_rev)}</strong><small>current and planned weekly revenue</small></div>
      <div className="summary-divider" />
      <div className="summary-stat tone-green"><span>Operating</span><strong>{integer.format(totals.operating)}</strong><small>{compactMoney.format(totals.operating_weekly_rev)} weekly</small></div>
      <div className="summary-stat"><span>Planned</span><strong>{integer.format(totals.planned)}</strong><small>{compactMoney.format(totals.planned_weekly_rev)} upside</small></div>
      <div className="summary-stat"><span>Aircraft assigned</span><strong>{integer.format(fleetTotal)}</strong><small>across {integer.format(totals.circuits)} circuits</small></div>
      <div className={`summary-stat${routesPlanned ? " tone-amber" : ""}`}><span>Routes to buy</span><strong>{integer.format(routesPlanned)}</strong><small>of {integer.format(totals.routes_owned + routesPlanned)} in circuits</small></div>
      <div className={`summary-stat${totals.unscheduled_waves ? " tone-amber" : ""}`}><span>Unscheduled waves</span><strong>{integer.format(totals.unscheduled_waves)}</strong><small>bought but not flying</small></div>
    </section>

    <section className="flat-section">
      <SectionHeader kicker="Hub portfolio" title="Where the circuits are" count={<>{board.length} hubs{boardTotal.issues ? ` · ${boardTotal.issues} need attention` : ""}</>} />
      <div className="hub-board">
        <Button className={`hub-tile${filters.hub === "all" ? " is-active" : ""}`} onClick={() => patch({ hub: "all" })}>
          <strong><Building2 size={13} /> All hubs</strong>
          <b>{compactMoney.format(boardTotal.weekly)}</b>
          <small>{integer.format(unhubbed.length)} circuits · {integer.format(boardTotal.aircraft)} aircraft</small>
          <i><span style={{ width: "100%" }} /></i>
        </Button>
        {board.map((tile) => <Button className={`hub-tile${filters.hub === tile.hub ? " is-active" : ""}`} key={tile.hub} onClick={() => patch({ hub: tile.hub })}>
          <strong>{hubLabel(tile.hub, country.get(tile.hub))}{tile.issues ? <em>{tile.issues}</em> : null}</strong>
          <b>{compactMoney.format(tile.weekly)}</b>
          <small>{integer.format(tile.circuits)} circuits · {integer.format(tile.aircraft)} aircraft</small>
          <i><span style={{ width: `${(tile.weekly / topHub) * 100}%` }} /></i>
        </Button>)}
        {!board.length && <EmptyState title="No circuits match" hint="Adjust or clear the active filters." />}
      </div>
    </section>

    <section className="flat-section">
      <SectionHeader kicker="Circuit portfolio" title={filters.hub === "all" ? "All circuits" : `${filters.hub} circuits`} count={<>{circuits.length} of {snapshot.circuits.length}</>} />
      <SegmentedControl className="haul-tabs" label="Circuit status" value={filters.status} onChange={(status) => patch({ status })}
        options={STATUS_TABS.map((tab) => ({ value: tab.key, label: <><span>{tab.label}</span><small>{integer.format(statusCounts(tab.key))}</small></> }))} />
      <FilterBar className="circuit-controls" active={activeFilters} onClear={() => setFilters(NO_FILTERS)} status={<small>Click a circuit to open it</small>}>
        <SearchInput value={filters.query} onChange={(query) => patch({ query })} placeholder="Search circuit, aircraft, hub, or destination" />
        <MenuSelect label="Aircraft" value={filters.model} onChange={(model) => patch({ model })} options={models} icon={Plane} />
        <MenuSelect label="Attention" value={filters.attention} onChange={(attention) => patch({ attention })} options={ATTENTION_OPTIONS} icon={Filter} />
        <MenuSelect label="Sort by" value={sort} onChange={setSort} groups={SORT_GROUPS} icon={SlidersHorizontal} />
      </FilterBar>
      <div className="circuit-board">
        {circuits.map((circuit) => <CircuitCard circuit={circuit} key={circuit.name} onOpen={() => setSelected(circuit.name)} />)}
        {!circuits.length && <EmptyState title="No circuits match" hint="Adjust or clear the active filters." />}
      </div>
    </section>
  </div>;
}

/** One circuit at a glance: what it earns, how much of it is actually built,
 *  and where it flies. */
function CircuitCard({ circuit, onOpen }: { circuit: NetworkCircuit; onOpen: () => void }) {
  const issues = circuitIssues(circuit);
  const pending = circuit.status === "planned";
  return <Button className={`circuit-card is-${circuit.status}`} onClick={onOpen}>
    <div className="circuit-card-head">
      <div><strong>{circuit.name}</strong><small>{circuit.aircraft_model} · {seatSummary(circuit)}</small></div>
      <span className={`status-tag is-${circuit.status}`}>{STATUS_LABEL[circuit.status] || circuit.status}</span>
    </div>
    <div className="circuit-card-rev">
      <b>{compactMoney.format(circuit.weekly_rev)}</b><span>per week</span>
    </div>
    <div className="circuit-card-meters">
      <Meter label="Routes" done={circuit.routes_owned} total={circuit.routes.length} />
      <Meter label="Waves" done={circuit.waves_scheduled} total={circuit.waves} />
      <Meter label="Fleet" done={circuit.aircraft} total={circuit.waves * 7} />
    </div>
    <div className="circuit-card-routes">{circuit.routes.map((route) => <em className={route.is_owned ? "is-owned" : ""} key={route.dest_iata}>{route.dest_iata}</em>)}</div>
    <div className="circuit-card-foot">
      {/* A planned circuit has every one of these gaps by definition, so its
          badges are the work list, not an alarm. */}
      {issues.length
        ? <span className={`circuit-flags${pending ? " is-pending" : ""}`}>{issues.map((issue) => <em key={issue}>{issue}</em>)}</span>
        : pending
          ? <span className="circuit-flags is-pending"><em>Not built yet</em></span>
          : <span className="circuit-flags is-healthy"><Check size={12} /> Healthy</span>}
      <small><Clock size={11} /> {circuit.total_hours || 0}h · {Math.round(circuit.avg_utilization)}%</small>
    </div>
  </Button>;
}

/** `done` of `total` as a number and a bar; a zero total draws an empty bar. */
function Meter({ label, done, total }: { label: string; done: number; total: number }) {
  const full = total > 0 && done >= total;
  return <div className={`circuit-meter${full ? " is-full" : ""}`}>
    <span>{label}</span>
    <b>{integer.format(done)}<i>/{integer.format(total)}</i></b>
    <u><span style={{ width: `${total > 0 ? Math.min(100, (done / total) * 100) : 0}%` }} /></u>
  </div>;
}

function CircuitDetail({ circuit, refreshToken, onBack, onOpenAircraft }: { circuit: NetworkCircuit; refreshToken: number; onBack: () => void; onOpenAircraft: (aircraftId: number) => void }) {
  const { data: pricing, error: liveError } = useApi(
    () => fetchPricing(circuit.hub_iata).then((prices) => new Map(prices.routes.map((route) => [route.iata, route]))),
    [circuit, refreshToken],
  );
  const [notice, setNotice] = useState<string | null>(null);

  useEffect(() => { window.scrollTo({ top: 0 }); }, [circuit.name]);

  const plannedAircraft = Math.max(0, circuit.waves * 7 - circuit.aircraft);
  const capacity = dailyCapacity([circuit]);
  const dailySeats = PRICE_CLASSES.map((cls) => capacity[cls]);
  const rows = circuit.routes.map((route) => {
    const live = pricing?.get(route.dest_iata);
    return { route, live, actual: live ? classRevenue(live.price, live.carried) : null, max: live ? classRevenue(live.price, live.demand) : null };
  });
  const sumClasses = (key: "actual" | "max") => PRICE_CLASSES.map((_, index) => rows.reduce((sum, row) => sum + (row[key]?.[index] ?? 0), 0));
  const actualTotal = sumClasses("actual"), maxTotal = sumClasses("max");
  const actualDaily = sum(actualTotal), maxDaily = sum(maxTotal);

  return <div className="stack-workspace circuit-detail">
    <Button className="detail-back" onClick={onBack}><ArrowLeft size={16} /> All circuits</Button>
    <header className="circuit-hero">
      <div className="circuit-hero-top">
        <div className="circuit-hero-id">
          <h2>{circuit.name}<span className={`status-tag is-${circuit.status}`}>{STATUS_LABEL[circuit.status] || circuit.status}</span></h2>
          <p>{circuit.hub_iata} hub · {circuit.aircraft_model} · {seatSummary(circuit)} seats</p>
        </div>
        <div className="circuit-hero-money">
          <div className="tone-green"><span>Revenue per day</span><strong>{pricing ? compactMoney.format(actualDaily) : "..."}</strong><small>{pricing ? `${Math.round((actualDaily / (maxDaily || 1)) * 100)}% of ${compactMoney.format(maxDaily)} at full demand` : "loading live prices"}</small></div>
          <div><span>Projected weekly</span><strong>{compactMoney.format(circuit.weekly_rev)}</strong><small>{compactMoney.format(circuit.weekly_rev / 7)} per day at plan</small></div>
        </div>
      </div>
      {/* Every stat reads "now / plan", so a half-built circuit shows the gap. */}
      <dl className="circuit-hero-stats">
        <HeroStat label="Routes" now={circuit.routes_owned} plan={circuit.routes.length} note="owned" />
        <HeroStat label="Waves" now={circuit.waves_scheduled} plan={circuit.waves} note="scheduled" />
        <HeroStat label="Aircraft" now={circuit.aircraft} plan={circuit.waves * 7} note={plannedAircraft ? `${integer.format(plannedAircraft)} to buy` : "fleet complete"} />
        <HeroStat label="Round trip" now={`${circuit.total_hours || 0}h`} plan="168h" note="of the week" />
        <HeroStat label="Utilization" now={`${Math.round(circuit.avg_utilization)}%`} note={circuit.idle_aircraft ? `${integer.format(circuit.idle_aircraft)} idle` : "none idle"} tone={circuit.idle_aircraft ? "amber" : "green"} />
      </dl>
    </header>

    <section className="flat-section"><SectionHeader kicker="Commercial detail" title="Routes in this circuit" count="E / B / F / C · revenue per day" />
      {liveError && <p className="circuit-live-note">Live route prices, carried volume and remaining demand are unavailable.</p>}
      <div className="grid-table-wrap circuit-route-table"><Table className="grid-table"><TableHeader><TableRow><TableHead>Route</TableHead><TableHead>Status</TableHead><TableHead>Current price</TableHead><TableHead>Daily seat capacity</TableHead><TableHead>Audited demand</TableHead><TableHead>Actual demand remaining</TableHead><TableHead>Actual revenue</TableHead><TableHead>Max revenue</TableHead></TableRow></TableHeader><TableBody>{rows.map(({ route, live, actual, max }) =>
        <TableRow key={route.dest_iata}><TableCell><strong>{circuit.hub_iata} → {route.dest_iata}</strong><small>{route.dest_name || `${integer.format(route.distance_km || 0)} km`}</small></TableCell><TableCell><span className={`status-tag is-${route.is_owned ? "completed" : "bought"}`}>{route.is_owned ? "Owned" : "Planned"}</span></TableCell><TableCell><ClassCells values={PRICE_CLASSES.map((key) => live?.price[key])} moneyValues /></TableCell><TableCell><ClassCells values={dailySeats} /></TableCell><TableCell><ClassCells values={[route.eco_demand, route.bus_demand, route.fir_demand, route.cargo_demand]} /></TableCell><TableCell><ClassCells values={PRICE_CLASSES.map((key) => live?.remaining[key])} /></TableCell><TableCell><ClassCells values={actual ?? []} moneyValues total /></TableCell><TableCell><ClassCells values={max ?? []} moneyValues total /></TableCell></TableRow>
      )}</TableBody><tfoot><TableRow><TableCell colSpan={6}><strong>Circuit total</strong><small>max = demand × current price</small></TableCell><TableCell><ClassCells values={actualTotal} moneyValues total /></TableCell><TableCell><ClassCells values={maxTotal} moneyValues total /></TableCell></TableRow></tfoot></Table></div>
    </section>

    <section className="flat-section circuit-aircraft-section">
      <SectionHeader kicker="Fleet assignment" title="Aircraft" count={<>{circuit.aircraft} assigned · {plannedAircraft} planned</>} />
      {/* Circuit aircraft are named "<circuit>-NN", so the trailing dash is what
          keeps MPM-C02 out of MPM-C022's fleet. */}
      <FleetBrowser className="is-embedded" refreshToken={refreshToken} scope={{ name_query: `${circuit.name}-` }}
        searchPlaceholder="Search this circuit's aircraft, model, or livery"
        notice={notice} onNotice={setNotice} onOpenAircraft={onOpenAircraft} />
    </section>
  </div>;
}

/** One "now / plan" stat in the circuit header; `plan` omitted means the value
 *  stands alone. */
function HeroStat({ label, now, plan, note, tone }: { label: string; now: number | string; plan?: number | string; note: string; tone?: string }) {
  const value = (input: number | string) => typeof input === "number" ? integer.format(input) : input;
  const full = plan !== undefined && typeof now === "number" && typeof plan === "number" && now >= plan;
  return <div className={`circuit-hero-stat${tone ? ` tone-${tone}` : ""}${full ? " is-full" : ""}`}>
    <dt>{label}</dt>
    <dd>{value(now)}{plan === undefined ? null : <i>/{value(plan)}</i>}</dd>
    <small>{note}</small>
  </div>;
}

/** Four class cells (E/B/F/C); `total` appends a fifth cell with their sum. */
function ClassCells({ values, moneyValues = false, total = false }: { values: (number | null | undefined)[]; moneyValues?: boolean; total?: boolean }) {
  const cells = values.length ? values : [null, null, null, null];
  const shown = total ? [...cells, values.length ? sum(cells) : null] : cells;
  return <span className={`class-values${total ? " has-total" : ""}`}>{shown.map((value, index) => <b key={index}>{value == null ? "n/a" : moneyValues ? compactMoney.format(value) : integer.format(value)}</b>)}</span>;
}

function sum(values: (number | null | undefined)[]) { return values.reduce<number>((acc, value) => acc + (value ?? 0), 0); }

function seatSummary(circuit: NetworkCircuit) { return `${circuit.seats.eco} / ${circuit.seats.bus} / ${circuit.seats.fir} / ${circuit.seats.cargo}T`; }
