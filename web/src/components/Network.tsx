import { useEffect, useMemo, useState } from "react";
import { ArrowLeft, Building2, Globe2, ListFilter, Map as MapIcon, MapPin, Route } from "lucide-react";
import { fetchNetwork, fetchPricing, fetchRouteDetail, fetchRouteShowline } from "../api";
import { NetworkLine, NetworkSnapshot, PriceClass, PricingRoute } from "../types";
import { CLASS_LABELS, PRICE_CLASSES, dailyCapacity, sumClasses } from "../classes";
import { integer, shortDate, wholeMoney } from "../format";
import { flagEmoji, hubLabel } from "../hubFlag";
import { FleetBrowser } from "./FleetBrowser";
import { FilterBar, SearchInput } from "./FilterBar";
import { MenuSelect } from "./MenuSelect";
import { EmptyState, ErrorState, LoadingState } from "./PageStates";
import { useApi } from "../useApi";
import { MapMode, RouteMap } from "./RouteMap";
import { SectionHeader } from "./SectionHeader";
import { SegmentedControl } from "./SegmentedControl";
import { Button } from "@/components/ui/button";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";

interface NetworkProps { refreshToken: number; onOpenAircraft: (aircraftId: number) => void }

const SORT_OPTIONS = [
  { value: "distance_desc", label: "Distance", hint: "Longest first" },
  { value: "distance_asc", label: "Distance", hint: "Shortest first" },
  { value: "route_asc", label: "Route", hint: "A to Z" },
  { value: "demand_desc", label: "Economy demand", hint: "Highest first" },
];

export function Network({ refreshToken, onOpenAircraft }: NetworkProps) {
  const { data: snapshot, error } = useApi(fetchNetwork, [refreshToken]);
  const [query, setQuery] = useState("");
  const [hub, setHub] = useState("all");
  const [sort, setSort] = useState("distance_desc");
  const [includePlanned, setIncludePlanned] = useState(false);
  const [mapMode, setMapMode] = useState<MapMode>("map");
  const [live, setLive] = useState<Record<string, PricingRoute>>({});
  const [liveDone, setLiveDone] = useState(0);
  const [liveError, setLiveError] = useState<string | null>(null);
  const [selected, setSelected] = useState<string | null>(null);

  // Remaining demand and per-line revenue are only in the live pricing read,
  // one mobile call per hub, so walk the hubs a few at a time and fill the
  // rows in as each hub lands instead of blocking the table on all of them.
  const liveHubs = useMemo(
    () => (hub === "all" ? (snapshot?.hubs ?? []).map((item) => item.hub_iata) : [hub]),
    [snapshot, hub],
  );

  useEffect(() => {
    if (!liveHubs.length) return;
    let alive = true;
    const queue = [...liveHubs];
    setLive({});
    setLiveDone(0);
    setLiveError(null);
    const worker = async () => {
      while (alive && queue.length) {
        const iata = queue.shift() as string;
        try {
          const data = await fetchPricing(iata);
          if (!alive) return;
          setLive((prev) => ({ ...prev, ...Object.fromEntries(data.routes.map((route) => [`${iata}-${route.iata}`, route] as const)) }));
        } catch (reason) {
          if (alive) setLiveError(reason instanceof Error ? reason.message : "live pricing unavailable");
        }
        if (alive) setLiveDone((count) => count + 1);
      }
    };
    void Promise.all([worker(), worker(), worker()]);
    return () => { alive = false; };
  }, [liveHubs, refreshToken]);

  const routes = useMemo(() => {
    const term = query.trim().toLowerCase();
    return [...(snapshot?.routes ?? [])].filter((line) =>
      (line.is_owned || includePlanned) &&
      (hub === "all" || line.hub_iata === hub) &&
      (!term || `${line.hub_iata} ${line.dest_iata} ${line.dest_name || ""} ${line.dest_country || ""}`.toLowerCase().includes(term))
    ).sort((a, b) => {
      if (sort === "distance_asc") return (a.distance_km || 0) - (b.distance_km || 0);
      if (sort === "route_asc") return `${a.hub_iata}${a.dest_iata}`.localeCompare(`${b.hub_iata}${b.dest_iata}`);
      if (sort === "demand_desc") return (b.eco_demand || 0) - (a.eco_demand || 0);
      return (b.distance_km || 0) - (a.distance_km || 0);
    });
  }, [snapshot, includePlanned, hub, query, sort]);

  if (error) return <ErrorState title="Network unavailable" message={error} />;
  if (!snapshot) return <LoadingState />;

  const openLine = snapshot.routes.find((line) => `${line.hub_iata}-${line.dest_iata}` === selected);
  if (openLine) return <RouteDetailView line={openLine} snapshot={snapshot} refreshToken={refreshToken}
    onBack={() => setSelected(null)} onOpenAircraft={onOpenAircraft} />;

  const planned = snapshot.routes.filter((line) => !line.is_owned && line.is_planned).length;
  const owned = snapshot.routes.filter((line) => line.is_owned);
  const countries = new Set(owned.map((line) => line.dest_country).filter(Boolean)).size;
  const totalKm = owned.reduce((sum, line) => sum + (line.distance_km || 0), 0);
  const ecoDemand = owned.reduce((sum, line) => sum + (line.eco_demand || 0), 0);
  const shownHubs = new Set(routes.map((line) => line.hub_iata)).size;
  const hubOptions = [{ value: "all", label: "All hubs", hint: `${snapshot.hubs.length}` }, ...snapshot.hubs.map((item) => ({ value: item.hub_iata, label: hubLabel(item.hub_iata, item.country_code), hint: item.routes_known ? `${item.routes_owned} routes` : "not scraped" }))];
  const hubCountry = new Map(snapshot.hubs.map((item) => [item.hub_iata, item.country_code]));
  const liveHint = liveError ? ` · live prices: ${liveError}` : liveDone < liveHubs.length ? ` · live prices ${liveDone}/${liveHubs.length} hubs` : "";
  const activeFilters = Number(hub !== "all") + Number(Boolean(query.trim())) + Number(includePlanned);

  return <div className="stack-workspace network-workspace">
    <section className="portfolio-strip" aria-label="Network summary">
      <div className="portfolio-lead"><span className="summary-label">Owned network</span><strong>{integer.format(owned.length)}</strong><small>routes across {integer.format(new Set(owned.map((line) => line.hub_iata)).size)} of {snapshot.hubs.length} hubs</small></div>
      <div className="summary-divider" />
      <div className="summary-stat"><span>Network distance</span><strong>{integer.format(totalKm)} km</strong><small>one-way route distance</small></div>
      <div className="summary-stat"><span>Countries served</span><strong>{integer.format(countries)}</strong><small>owned destinations</small></div>
      <div className="summary-stat"><span>Economy demand</span><strong>{integer.format(ecoDemand)}</strong><small>daily seats audited</small></div>
      <div className={`summary-stat${planned ? " tone-amber" : ""}`}><span>Planned routes</span><strong>{integer.format(planned)}</strong><small>not yet bought</small></div>
    </section>

    <section className="flat-section route-map-section">
      <SectionHeader kicker="Route geography" title={mapMode === "map" ? "Network map" : "Network globe"}>
        <div className="section-header-controls">
          <Button className={`toggle-control${includePlanned ? " is-active" : ""}`} role="switch" aria-checked={includePlanned} onClick={() => setIncludePlanned((value) => !value)}><span /><Route size={15} /> Include planned</Button>
          <SegmentedControl className="view-switch" label="Map display mode" value={mapMode} onChange={setMapMode} options={[
            { value: "map", label: <MapIcon size={16} />, title: "Flat map" },
            { value: "globe", label: <Globe2 size={16} />, title: "Globe" },
          ]} />
        </div>
      </SectionHeader>
      <RouteMap routes={routes} hubs={snapshot.hubs} mode={mapMode} />
      <div className="map-legend"><span><i className="is-owned" /> Owned</span>{includePlanned && <span><i className="is-planned" /> Planned</span>}<span><i className="is-hub" /> Hub</span><small>{mapMode === "map" ? "Scroll to zoom, drag to pan, double-click to zoom in" : "Drag to rotate, scroll to zoom"} · {routes.filter((line) => line.origin && line.destination).length} of {routes.length} routes mapped{snapshot.map_error ? " · coordinates unavailable while mobile is offline" : ""}</small></div>
    </section>

    <section className="flat-section">
      <SectionHeader kicker="Line inventory" title="Routes" count={<>{routes.length} shown · {shownHubs} hubs{liveHint}</>} />
      <FilterBar className="network-controls" active={activeFilters} idleLabel="Owned routes only" status={<small>Click a route to open it</small>}
        onClear={() => { setHub("all"); setQuery(""); setIncludePlanned(false); }}>
        <SearchInput value={query} onChange={setQuery} placeholder="Search route, airport, or country" />
        <MenuSelect label="Hub" value={hub} onChange={setHub} options={hubOptions} icon={Building2} />
        <MenuSelect label="Sort by" value={sort} onChange={setSort} options={SORT_OPTIONS} icon={ListFilter} />
      </FilterBar>
      <div className="grid-table-wrap network-route-table"><Table className="grid-table"><TableHeader><TableRow><TableHead>Route</TableHead><TableHead>Destination</TableHead><TableHead>Status</TableHead><TableHead className="is-numeric">Distance</TableHead><TableHead className="is-numeric">Eco left</TableHead><TableHead className="is-numeric">Bus left</TableHead><TableHead className="is-numeric">First left</TableHead><TableHead className="is-numeric">Cargo left</TableHead><TableHead className="is-numeric">Revenue/day</TableHead><TableHead className="is-numeric">Revenue/week</TableHead></TableRow></TableHeader><TableBody>
        {routes.map((line) => {
          const priced = live[`${line.hub_iata}-${line.dest_iata}`];
          return <TableRow className="is-clickable" onClick={() => setSelected(`${line.hub_iata}-${line.dest_iata}`)} key={`${line.hub_iata}-${line.dest_iata}`}><TableCell><strong>{flagEmoji(hubCountry.get(line.hub_iata))} {line.hub_iata} → {flagEmoji(line.dest_country)} {line.dest_iata}</strong></TableCell><TableCell className="is-destination">{line.dest_name || line.dest_iata}</TableCell><TableCell><span className={`status-tag is-${line.is_owned ? "completed" : "bought"}`}>{line.is_owned ? "Owned" : "Planned"}</span></TableCell><TableCell className="is-numeric">{line.distance_km ? `${integer.format(line.distance_km)} km` : "n/a"}</TableCell>
            {PRICE_CLASSES.map((cls) => remainingCell(priced, cls))}
            <TableCell className="is-numeric">{priced ? wholeMoney.format(priced.daily_revenue) : <span className="is-dim">…</span>}</TableCell>
            <TableCell className="is-numeric">{priced ? wholeMoney.format(priced.weekly_revenue) : <span className="is-dim">…</span>}</TableCell></TableRow>;
        })}
        {!routes.length && <TableRow><TableCell colSpan={10}><EmptyState icon={MapPin} title="No routes match" hint="Adjust or clear the active filters." /></TableCell></TableRow>}
      </TableBody></Table></div>
    </section>
  </div>;
}

/** Remaining demand for one class: green when seats are still unsold, red when
 *  the line carries more than the demand it audited (overfilled). */
function remainingCell(priced: PricingRoute | undefined, cls: PriceClass) {
  const value = priced?.remaining?.[cls];
  if (value == null) return <TableCell className="is-numeric is-dim" key={cls}>…</TableCell>;
  return <TableCell className={`is-numeric ${value < 0 ? "is-overfilled" : "is-unsold"}`} key={cls}>{integer.format(value)}</TableCell>;
}



/** `03/11/2024 at 23h01` (the game's own format) -> `03/11/2024`. */
function showlineDate(value: string | null | undefined): string {
  return value ? value.split(" at ")[0] : "n/a";
}

/** Rows of the game's Today/Yesterday statistics block, in its own order. */
const HEADLINE_ROWS: [string, string][] = [
  ["turnover", "Turnover"], ["cost", "Cost"], ["ancillary", "Ancillary revenue"],
  ["flight_profit", "Flight profits"], ["incidents", "Cost of incidents"],
];
const HISTORY_ROWS: [string, string][] = [
  ["demand", "Demand"], ["offer", "Offer"], ["price", "Average price"],
  ["ticket_sales", "Ticket sales"], ["cargo", "Cargo"], ["ancillary", "Ancillary revenue"],
  ["turnover", "Turnover"], ["cost", "Cost"], ["result", "Result"],
  ["incidents", "Cost of incidents"],
];
const MONEY_ROWS = new Set(["price", "ticket_sales", "cargo", "ancillary", "turnover", "cost", "result", "incidents"]);
const WEEK_ROWS: [string, string, boolean][] = [
  ["turnover", "Turnover", false], ["ticket_turnover", "including ticket sale turnover", true],
  ["ancillary", "including ancillary revenue", true], ["flight_costs", "Flight costs", false],
  ["fuel_costs", "including fuel costs", true], ["airport_taxes", "including airport taxes", true],
  ["other_costs", "including other costs", true], ["flight_result", "Flight results", false],
];

/** One route, the way the game's own ROUTE DETAILS page shows it. Three reads
 *  feed it, each rendering as it lands: the cached row + `line/{id}` for what
 *  the route cost and what the audit says, `/api/pricing/{hub}` for the live
 *  per-class price and remaining demand, and the `/network/showline` scrape for
 *  everything only that page has — taxes, flights per week, the turnover
 *  history and forecast, the 7-day financials, and the real aircraft list. */
function RouteDetailView({ line, snapshot, refreshToken, onBack, onOpenAircraft }: {
  line: NetworkLine; snapshot: NetworkSnapshot; refreshToken: number;
  onBack: () => void; onOpenAircraft: (aircraftId: number) => void;
}) {
  const deps = [line.hub_iata, line.dest_iata, refreshToken];
  const { data: detail, error: detailError } = useApi(() => fetchRouteDetail(line.hub_iata, line.dest_iata), deps);
  const { data: priced, error: pricedError } = useApi(
    () => fetchPricing(line.hub_iata).then((prices) => prices.routes.find((route) => route.iata === line.dest_iata) ?? null), deps);
  const { data: showline, error: showlineError } = useApi(() => fetchRouteShowline(line.hub_iata, line.dest_iata), deps);
  const page = showline?.details ?? null;
  const pageError = showlineError ?? showline?.error ?? null;
  const liveError = pricedError && "Live price, carried volume and remaining demand are unavailable.";
  const [day, setDay] = useState<"today" | "yesterday">("today");
  const [notice, setNotice] = useState<string | null>(null);

  useEffect(() => { window.scrollTo({ top: 0 }); }, [line.hub_iata, line.dest_iata]);

  const circuits = snapshot.circuits.filter((circuit) => line.circuits.includes(circuit.name));
  const capacity = dailyCapacity(circuits);
  const profile = detail?.line ?? null;
  const stats = page?.per_class[day];
  const price = (cls: PriceClass) => stats?.price[cls] ?? priced?.price[cls] ?? profile?.price[cls] ?? null;
  const carried = (cls: PriceClass) => priced?.carried[cls] ?? null;
  const remaining = (cls: PriceClass) => priced?.remaining[cls] ?? profile?.remaining[cls] ?? null;
  const demand = (cls: PriceClass) => stats?.demand[cls] ?? priced?.demand[cls] ?? profile?.demand[cls] ?? null;
  // The game's own turnover beats price x carried whenever the page is there.
  const turnover = (cls: PriceClass) => stats?.turnover[cls] ?? (priced ? (price(cls) ?? 0) * (carried(cls) ?? 0) : null);
  const dayTurnover = page ? page.totals[day].turnover ?? null : priced?.daily_revenue ?? null;
  const aircraft = page?.aircraft ?? circuits.reduce((sum, circuit) => sum + circuit.aircraft, 0);
  const resaleDelta = profile?.selling_price && profile.purchase_price
    ? Math.round((profile.selling_price / profile.purchase_price - 1) * 100) : null;
  const cell = (value: number | null | undefined, asMoney = false) =>
    value == null ? <span className="is-dim">n/a</span> : asMoney ? wholeMoney.format(value) : integer.format(value);

  return <div className="stack-workspace circuit-detail route-detail">
    <Button className="detail-back" onClick={onBack}><ArrowLeft size={16} /> All routes</Button>
    <section className="circuit-detail-head">
      <div className="detail-identity">
        <p className="section-kicker">{line.hub_iata} hub
          <span className={`status-tag is-${line.is_owned ? "completed" : "bought"}`}>{line.is_owned ? "Owned" : "Planned"}</span>
          {profile?.is_frozen ? <span className="status-tag is-planned">Frozen</span> : null}
        </p>
        <h2>{flagEmoji(snapshot.hubs.find((item) => item.hub_iata === line.hub_iata)?.country_code)} {line.hub_iata} → {flagEmoji(line.dest_country)} {line.dest_iata}</h2>
        <small>{page?.route || line.dest_name || line.dest_iata}{line.circuits.length ? ` · ${line.circuits.join(", ")}` : " · not in a circuit"}{profile?.incidents ? ` · ${integer.format(profile.incidents)} incidents` : ""}</small>
      </div>
      <div className="detail-figures">
        <div className="tone-green"><span>Turnover {day}</span><strong>{dayTurnover == null ? "..." : wholeMoney.format(dayTurnover)}</strong><small>{page ? `${wholeMoney.format(page.totals[day].flight_profit ?? 0)} flight profit` : "price x carried, all classes"}</small></div>
        <div><span>Turnover / 7 days</span><strong>{page ? wholeMoney.format(page.week.turnover ?? 0) : priced ? wholeMoney.format(priced.weekly_revenue) : "..."}</strong><small>{page ? `${wholeMoney.format(page.week.flight_result ?? 0)} result` : "at today's carried volume"}</small></div>
      </div>
    </section>

    <section className="fleet-summary is-six">
      <div className="fleet-stat tone-cyan"><span>Distance</span><div><strong>{line.distance_km ? `${integer.format(line.distance_km)} km` : "n/a"}</strong><small>{page?.categories.length ? `categories ${page.categories[0]} to ${page.categories[page.categories.length - 1]}` : "one way"}</small></div></div>
      <div className="fleet-stat"><span>Aircraft</span><div><strong>{integer.format(aircraft)}</strong><small>{page?.flights_per_week != null ? `${integer.format(page.flights_per_week)} flights per week` : `${circuits.reduce((sum, c) => sum + c.waves, 0)} waves assigned`}</small></div></div>
      <div className="fleet-stat"><span>Airport taxes</span><div><strong>{page?.taxes == null ? "..." : wholeMoney.format(page.taxes)}</strong><small>per flight</small></div></div>
      <div className="fleet-stat"><span>Purchase price</span><div><strong>{profile ? wholeMoney.format(profile.purchase_price ?? 0) : "..."}</strong><small>bought {page?.purchased_at ? showlineDate(page.purchased_at) : shortDate(profile?.purchased_at)}</small></div></div>
      <div className="fleet-stat"><span>Resale value</span><div><strong>{profile ? wholeMoney.format(profile.selling_price ?? 0) : "..."}</strong><small>{resaleDelta == null ? "if sold back" : `${resaleDelta > 0 ? "+" : ""}${resaleDelta}% of purchase`}</small></div></div>
      <div className={`fleet-stat${profile && (profile.audit.reliability ?? 0) < 100 ? " tone-amber" : ""}`}><span>Audit reliability</span><div><strong>{profile?.audit.reliability == null ? "..." : `${profile.audit.reliability}%`}</strong><small>audited {shortDate(profile?.audit.date)}</small></div></div>
    </section>

    {(detailError || detail?.error || liveError || pageError) && <div className="route-notes">
      {[detailError, detail?.error, liveError, pageError].filter(Boolean).map((message) => <p className="circuit-live-note" key={message}>{message}</p>)}
    </div>}

    <section className="flat-section">
      <SectionHeader kicker="Statistics" title="By class" count={page ? undefined : "live prices · per day"}>
        {page && <SegmentedControl className="view-switch is-text" label="Statistics day" value={day} onChange={setDay} options={[
          { value: "today", label: "Today" },
          { value: "yesterday", label: "Yesterday" },
        ]} />}
      </SectionHeader>
      <div className="grid-table-wrap"><Table className="grid-table route-class-table"><TableHeader><TableRow>
        <TableHead>Class</TableHead><TableHead className="is-numeric">Average price</TableHead><TableHead className="is-numeric">Ideal price</TableHead>
        <TableHead className="is-numeric">Demand</TableHead><TableHead className="is-numeric">Audited demand</TableHead>
        <TableHead className="is-numeric">Offer</TableHead><TableHead className="is-numeric">Daily capacity</TableHead>
        <TableHead className="is-numeric">Carried</TableHead><TableHead className="is-numeric">Remaining</TableHead>
        <TableHead className="is-numeric">Ancillary</TableHead><TableHead className="is-numeric">Turnover</TableHead>
      </TableRow></TableHeader><TableBody>
        {PRICE_CLASSES.map((cls) => <TableRow key={cls}>
          <TableCell><strong>{CLASS_LABELS[cls]}</strong></TableCell>
          <TableCell className="is-numeric">{cell(price(cls), true)}</TableCell>
          <TableCell className="is-numeric">{cell(priced?.audit_price[cls] ?? profile?.audit.price[cls], true)}</TableCell>
          <TableCell className="is-numeric">{cell(demand(cls))}</TableCell>
          <TableCell className="is-numeric">{cell(profile?.audit.demand[cls])}</TableCell>
          <TableCell className="is-numeric">{cell(stats?.offer[cls])}</TableCell>
          <TableCell className="is-numeric">{circuits.length ? integer.format(capacity[cls]) : <span className="is-dim">n/a</span>}</TableCell>
          <TableCell className="is-numeric">{cell(carried(cls))}</TableCell>
          <TableCell className={`is-numeric ${(remaining(cls) ?? 0) < 0 ? "is-overfilled" : "is-unsold"}`}>{cell(remaining(cls))}</TableCell>
          <TableCell className="is-numeric">{cell(stats?.ancillary[cls], true)}</TableCell>
          <TableCell className="is-numeric">{cell(turnover(cls), true)}</TableCell>
        </TableRow>)}
      </TableBody><tfoot><TableRow>
        <TableCell colSpan={5}><strong>Total</strong><small>price, offer, ancillary and turnover are the game's own {day} figures; carried and remaining come from the live pricing read</small></TableCell>
        <TableCell className="is-numeric">{cell(stats ? sumClasses(stats.offer) : null)}</TableCell>
        <TableCell className="is-numeric">{circuits.length ? integer.format(sumClasses(capacity)) : <span className="is-dim">n/a</span>}</TableCell>
        <TableCell className="is-numeric">{cell(priced ? PRICE_CLASSES.reduce((sum, cls) => sum + (carried(cls) ?? 0), 0) : null)}</TableCell>
        <TableCell className="is-numeric">{cell(priced ? PRICE_CLASSES.reduce((sum, cls) => sum + (remaining(cls) ?? 0), 0) : null)}</TableCell>
        <TableCell className="is-numeric">{cell(stats ? sumClasses(stats.ancillary) : null, true)}</TableCell>
        <TableCell className="is-numeric">{cell(dayTurnover, true)}</TableCell>
      </TableRow></tfoot></Table></div>
    </section>

    {page && <section className="flat-section">
      <SectionHeader kicker="Daily results" title="Today against yesterday" count="whole route" />
      <div className="grid-table-wrap"><Table className="grid-table"><TableHeader><TableRow>
        <TableHead></TableHead><TableHead className="is-numeric">Today</TableHead><TableHead className="is-numeric">Yesterday</TableHead><TableHead className="is-numeric">Change</TableHead>
      </TableRow></TableHeader><TableBody>
        {HEADLINE_ROWS.map(([key, label]) => {
          const now = page.totals.today[key] ?? 0, before = page.totals.yesterday[key] ?? 0;
          const delta = before ? Math.round((now / before - 1) * 100) : null;
          return <TableRow key={key}>
            <TableCell><strong>{label}</strong></TableCell>
            <TableCell className="is-numeric">{wholeMoney.format(now)}</TableCell>
            <TableCell className="is-numeric">{wholeMoney.format(before)}</TableCell>
            <TableCell className={`is-numeric ${delta == null ? "is-dim" : delta < 0 ? "is-overfilled" : "is-unsold"}`}>{delta == null ? "n/a" : `${delta > 0 ? "+" : ""}${delta}%`}</TableCell>
          </TableRow>;
        })}
      </TableBody></Table></div>
    </section>}

    {page && page.history.dates.length > 0 && <section className="flat-section">
      <SectionHeader kicker="History and forecast" title="Last six days" count="the game's own daily totals" />
      <div className="grid-table-wrap"><Table className="grid-table route-history-table"><TableHeader><TableRow>
        <TableHead>Total</TableHead>{page.history.dates.map((date, index) => <TableHead className="is-numeric" key={date}>
          <strong>{index === page.history.dates.length - 1 ? "Today" : index === page.history.dates.length - 2 ? "Yesterday" : `D-${page.history.dates.length - 1 - index}`}</strong><small>{date}</small>
        </TableHead>)}
      </TableRow></TableHeader><TableBody>
        {HISTORY_ROWS.filter(([key]) => page.history.rows[key]).map(([key, label]) => <TableRow key={key}>
          <TableCell><strong>{label}</strong></TableCell>
          {page.history.rows[key].map((value, index) => <TableCell className="is-numeric" key={index}>{cell(value, MONEY_ROWS.has(key))}</TableCell>)}
        </TableRow>)}
        {page.forecast.turnover && <TableRow className="is-forecast">
          <TableCell><strong>Forecast turnover</strong><small>{page.forecast.dates.join(" · ")}</small></TableCell>
          {page.forecast.turnover.map((value, index) => <TableCell className="is-numeric" key={index}>{cell(value, true)}</TableCell>)}
        </TableRow>}
      </TableBody></Table></div>
    </section>}

    {page && <section className="flat-section">
      <SectionHeader kicker="Financial summary" title="Over 7 days" count={<>{wholeMoney.format(page.week.flight_result ?? 0)} flight result</>} />
      <div className="route-week">
        <div className="route-week-col">
          <h3>Financial details</h3>
          {WEEK_ROWS.map(([key, label, indented]) => <div className={`route-week-row${indented ? " is-sub" : ""}`} key={key}>
            <span>{label}</span><b className={(page.week[key] ?? 0) < 0 ? "is-overfilled" : ""}>{wholeMoney.format(page.week[key] ?? 0)}</b>
          </div>)}
        </div>
        <div className="route-week-col">
          <h3>Passengers and load</h3>
          <div className="route-week-row"><span>Economy class</span><b>{integer.format(page.week.pax_eco ?? 0)} pax</b></div>
          <div className="route-week-row"><span>Business class</span><b>{integer.format(page.week.pax_bus ?? 0)} pax</b></div>
          <div className="route-week-row"><span>First class</span><b>{integer.format(page.week.pax_first ?? 0)} pax</b></div>
          <div className="route-week-row"><span>Cargo</span><b>{integer.format(page.week.cargo_tons ?? 0)} T</b></div>
          <div className="route-week-row"><span>Cost of incidents</span><b className={(page.week.incidents ?? 0) ? "is-overfilled" : ""}>{wholeMoney.format(page.week.incidents ?? 0)}</b></div>
        </div>
      </div>
    </section>}

    <section className="flat-section circuit-aircraft-section">
      <SectionHeader kicker="Fleet assignment" title="Aircraft on this route" count={page ? `${page.aircraft_list.length} flying it` : circuits.length ? circuits.map((c) => c.name).join(", ") : "no circuit"} />
      {/* The scraped page lists the aircraft actually scheduled on the line —
          which nothing else can answer: the mobile API only goes the other way
          (aircraft -> lines), and walking 100 planning pages per route is not
          worth it. Without Chrome, fall back to the circuit's "<name>-" fleet. */}
      {page
        ? <div className="route-aircraft-grid">{page.aircraft_list.map((item) => <Button className="route-aircraft" key={item.aircraft_id} onClick={() => onOpenAircraft(item.aircraft_id)}>
            <strong>{item.name || item.aircraft_id}{item.in_flight ? <em>In flight</em> : null}</strong>
            <small>{item.model}{item.seats ? ` · ${item.seats.total} seats (${item.seats.eco}/${item.seats.bus}/${item.seats.first})` : ""}{item.cargo_t ? ` · ${integer.format(item.cargo_t)}T` : ""}</small>
            <div>
              <span>Use<b>{item.use_pct == null ? "n/a" : `${item.use_pct}%`}</b></span>
              <span>Result<b>{cell(item.result, true)}</b></span>
              <span>Wear<b>{item.wear_pct == null ? "n/a" : `${item.wear_pct}%`}</b></span>
              <span>Age<b>{item.age || "n/a"}</b></span>
            </div>
          </Button>)}
          {!page.aircraft_list.length && <EmptyState icon={MapPin} title="No aircraft are scheduled on this route" />}
        </div>
        : circuits.length > 0
          ? <FleetBrowser className="is-embedded" refreshToken={refreshToken} scope={{ name_query: `${circuits[0].name}-` }}
              searchPlaceholder="Search this route's aircraft, model, or livery"
              notice={notice} onNotice={setNotice} onOpenAircraft={onOpenAircraft} />
          : <EmptyState icon={MapPin} title="No aircraft to show" hint="This route is in no circuit, and the game's route page is unavailable." />}
    </section>
  </div>;
}
