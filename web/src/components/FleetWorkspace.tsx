import React, { useEffect, useMemo, useRef, useState } from "react";
import {
  ArrowDownAZ,
  ArrowDownWideNarrow,
  ArrowUpAZ,
  ArrowUpNarrowWide,
  Building2,
  CalendarDays,
  Check,
  ChevronLeft,
  ChevronRight,
  Copy,
  Dices,
  Filter,
  Gauge,
  Layers,
  LayoutGrid,
  List,
  Package,
  Plane,
  RefreshCcw,
  Search,
  SlidersHorizontal,
  Sparkles,
  Tag,
  X,
} from "lucide-react";
import { fetchAircraftPurchaseDate, fetchDailyLiveries, fetchFleetPage, fetchPurchaseBackfillStatus, fetchStats, triggerSyncFleet, updateAircraftTags } from "../api";
import type { PurchaseBackfillStatus } from "../api";
import { CommandCenterSnapshot, DailyLivery, FleetAircraft, FleetStats, HaulTab } from "../types";
import { clearDailyLiverySeed, readDailyLiverySeed, writeDailyLiverySeed } from "../dailyLiverySeed";
import { hubLabel } from "../hubFlag";
import { splitLiveryName as splitRawLiveryName } from "../liveryName";
import { MenuOption, MenuSelect } from "./MenuSelect";
import { TagPicker } from "./TagPicker";

interface FleetWorkspaceProps {
  snapshot: CommandCenterSnapshot | null;
  initialPreset?: string;
  refreshToken: number;
  onDataChanged: () => void;
  onOpenLivery: (skinId: number) => void;
  onOpenAircraft: (aircraftId: number) => void;
}

type UtilizationFilter = "all" | "active" | "idle" | "partial" | "full";
type ViewMode = "grid" | "list";

// A showcase card names at most this many hubs; the rest collapse into a "+N"
// chip whose tooltip spells them out, so a livery spread over eight bases
// cannot push the card taller than the two beside it.
const DAILY_HUB_CHIPS = 2;
const GRID_CARD_MIN_WIDTH = 275;
const GRID_GAP = 12;
const GRID_ROWS_PER_PAGE = 8;
const integer = new Intl.NumberFormat(undefined, { maximumFractionDigits: 0 });
const HAUL_TABS: { key: HaulTab; label: string }[] = [
  { key: "all", label: "All aircraft" },
  { key: "short", label: "Short haul" },
  { key: "medium", label: "Medium haul" },
  { key: "long", label: "Long haul" },
  { key: "cargo", label: "Cargo" },
];

// Sort choices in menu order. Name ascending is the default, name descending
// sits right under it, and every other field reads "most first" then "least
// first" so a direction never has to be decoded from a + / - suffix.
const SORT_GROUPS = [
  {
    label: "Name",
    options: [
      { value: "name_asc", label: "Name", hint: "A to Z", icon: ArrowDownAZ },
      { value: "name_desc", label: "Name", hint: "Z to A", icon: ArrowUpAZ },
    ],
  },
  {
    label: "Condition",
    options: [
      { value: "use_desc", label: "Utilization", hint: "Busiest first", icon: ArrowDownWideNarrow },
      { value: "use_asc", label: "Utilization", hint: "Idlest first", icon: ArrowUpNarrowWide },
    ],
  },
  {
    label: "Aircraft",
    options: [
      { value: "model_asc", label: "Model", hint: "A to Z", icon: Plane },
    ],
  },
  {
    label: "History",
    options: [
      { value: "purchased_desc", label: "Purchased", hint: "Newest first", icon: ArrowDownWideNarrow },
      { value: "purchased_asc", label: "Purchased", hint: "Oldest first", icon: ArrowUpNarrowWide },
    ],
  },
];
const DEFAULT_SORT = SORT_GROUPS[0].options[0].value;

const STATUS_OPTIONS: MenuOption[] = [
  { value: "all", label: "All utilization" },
  { value: "idle", label: "Idle only", hint: "0%" },
  { value: "partial", label: "Partially scheduled", hint: "under 100%" },
  { value: "full", label: "Fully utilized", hint: "100%" },
  { value: "active", label: "Any active", hint: "above 0%" },
];

const LIVERY_OPTIONS: MenuOption[] = [
  { value: "all", label: "All liveries" },
  { value: "special", label: "Special" },
  { value: "manufacturer", label: "Manufacturer" },
];

function cleanAircraftName(value: string) {
  return value.replace(/\s+/g, " ").trim();
}

function splitLiveryName(value?: string | null): { model: string; livery: string } {
  const name = value?.trim();
  if (!name) return { model: "", livery: "Unknown livery" };
  return splitRawLiveryName(name);
}

function cleanLiveryName(value?: string | null) {
  return splitLiveryName(value).livery;
}

function formatTime(value: string) {
  const date = new Date(`${value.replace(" ", "T")}Z`);
  return Number.isNaN(date.valueOf())
    ? value
    : date.toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
}

function formatPurchaseDate(value: string) {
  // The API returns six fractional digits; JavaScript Date accepts three.
  const normalized = value.replace(" ", "T").replace(/\.(\d{3})\d+$/, ".$1");
  const date = new Date(`${normalized}Z`);
  return Number.isNaN(date.valueOf())
    ? value.split(" ")[0]
    : date.toLocaleDateString([], { dateStyle: "medium" });
}

function PurchaseDate({ aircraft, backfilling, onLoaded, onError }: {
  aircraft: FleetAircraft;
  backfilling?: boolean;
  onLoaded: (aircraftId: number, purchasedAt: string) => void;
  onError: (message: string) => void;
}) {
  const [loading, setLoading] = useState(false);

  if (aircraft.purchased_at) {
    return (
      <span className="purchase-date" title={`Purchased ${aircraft.purchased_at} UTC`}>
        <CalendarDays size={11} /> Purchased {formatPurchaseDate(aircraft.purchased_at)}
      </span>
    );
  }

  const load = async (event: React.MouseEvent<HTMLButtonElement>) => {
    event.stopPropagation();
    setLoading(true);
    try {
      const result = await fetchAircraftPurchaseDate(aircraft.aircraft_id);
      onLoaded(aircraft.aircraft_id, result.purchased_at);
    } catch (reason) {
      onError(reason instanceof Error ? reason.message : "Purchase date request failed");
    } finally {
      setLoading(false);
    }
  };

  if (backfilling && !loading) {
    return (
      <span className="purchase-date" title="The background sweep is fetching this date">
        <CalendarDays size={11} /> Loading purchase date...
      </span>
    );
  }

  return (
    <button className="purchase-date is-action" onClick={load} disabled={loading} title="Fetch and cache the exact purchase date">
      <CalendarDays size={11} /> {loading ? "Loading date..." : "Load purchase date"}
    </button>
  );
}

function liveryBadge(aircraft: FleetAircraft) {
  if (!aircraft.skin_id) return { label: "Unknown livery", tone: "unknown" };
  if (/manufacturer|constructeur/i.test(`${aircraft.skin_name || ""} ${aircraft.skin_img || ""} ${aircraft.skin_picture_path || ""}`)) {
    return { label: "Manufacturer", tone: "manufacturer" };
  }
  if (aircraft.skin_name) return { label: cleanLiveryName(aircraft.skin_name), tone: "special" };
  return { label: "Manufacturer", tone: "manufacturer" };
}

// "356 (199 / 110 / 47)" -- total seats up front, the eco/bus/first split in
// parentheses. Cargo aircraft naturally read "0 (0 / 0 / 0)".
function seatConfig(aircraft: FleetAircraft): string {
  const eco = aircraft.seats_eco ?? 0;
  const bus = aircraft.seats_bus ?? 0;
  const first = aircraft.seats_first ?? 0;
  return `${eco + bus + first} (${eco} / ${bus} / ${first})`;
}

function cargoTonnage(aircraft: FleetAircraft): number {
  return aircraft.payload_t ?? aircraft.max_tonnage ?? 0;
}

// Tiles and list rows read the configuration the same way: "Seats: 356 (199 /
// 110 / 47)" alongside "Cargo: 25T", in the same type size, so switching view
// mode never re-teaches the numbers. Only the axis differs -- the list column
// stacks the two lines (see .fleet-table .aircraft-config in index.css).
function AircraftConfig({ aircraft }: { aircraft: FleetAircraft }) {
  return (
    <div className="aircraft-config">
      <span className="config-value">Seats: {aircraft.seats_eco != null || aircraft.is_cargo ? seatConfig(aircraft) : "n/a"}</span>
      <span className="config-value">Cargo: {cargoTonnage(aircraft)}T</span>
    </div>
  );
}

function haulLabel(aircraft: FleetAircraft) {
  if (aircraft.is_cargo) return "Cargo";
  if (aircraft.haul === "short") return "Short haul";
  if (aircraft.haul === "medium") return "Medium haul";
  if (aircraft.haul === "long") return "Long haul";
  return "Unknown haul";
}

function AircraftArtwork({ skinId, alt, featured = false }: { skinId: number | null; alt: string; featured?: boolean }) {
  const [failed, setFailed] = useState(false);
  return (
    <div className={`aircraft-artwork${featured ? " is-featured" : ""}`}>
      {skinId && !failed ? (
        <img src={`/api/skin_image/${skinId}`} alt={alt} loading="lazy" onError={() => setFailed(true)} />
      ) : (
        <Plane size={featured ? 36 : 27} />
      )}
    </div>
  );
}

export function FleetWorkspace({ snapshot, initialPreset, refreshToken, onDataChanged, onOpenLivery, onOpenAircraft }: FleetWorkspaceProps) {
  const workspaceRef = useRef<HTMLDivElement>(null);
  const [items, setItems] = useState<FleetAircraft[]>([]);
  const [total, setTotal] = useState(0);
  const [stats, setStats] = useState<FleetStats | null>(null);
  const [dailyLiveries, setDailyLiveries] = useState<DailyLivery[]>([]);
  const [rerolling, setRerolling] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [debouncedQuery, setDebouncedQuery] = useState("");
  const [hub, setHub] = useState("all");
  const [utilization, setUtilization] = useState<UtilizationFilter>(initialPreset === "idle" ? "idle" : "all");
  const [haul, setHaul] = useState<HaulTab>("all");
  const [skin, setSkin] = useState<"all" | "special" | "manufacturer">("all");
  const [tag, setTag] = useState("all");
  const [sort, setSort] = useState(DEFAULT_SORT);
  const [viewMode, setViewMode] = useState<ViewMode>("grid");
  const [page, setPage] = useState(0);
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [syncing, setSyncing] = useState(false);
  const [backfill, setBackfill] = useState<PurchaseBackfillStatus | null>(null);
  const [backfillWatch, setBackfillWatch] = useState(0);
  const [notice, setNotice] = useState<string | null>(null);
  const [tagging, setTagging] = useState(false);
  const [gridColumns, setGridColumns] = useState(1);
  const pageSize = gridColumns * GRID_ROWS_PER_PAGE;
  // The parent re-creates onDataChanged every render; the poll loop must not
  // restart because of that.
  const dataChangedRef = useRef(onDataChanged);
  dataChangedRef.current = onDataChanged;

  useEffect(() => {
    const workspace = workspaceRef.current;
    if (!workspace) return;
    const measure = () => setGridColumns(Math.max(1, Math.floor((workspace.clientWidth + GRID_GAP) / (GRID_CARD_MIN_WIDTH + GRID_GAP))));
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(workspace);
    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    if (!initialPreset) return;
    if (initialPreset === "idle") {
      setUtilization("idle");
    } else if (initialPreset.startsWith("livery:")) {
      setQuery(initialPreset.slice("livery:".length));
    } else if (initialPreset.startsWith("name:")) {
      setQuery(initialPreset.slice("name:".length));
    }
  }, [initialPreset]);

  useEffect(() => {
    const timer = window.setTimeout(() => setDebouncedQuery(query.trim()), 220);
    return () => window.clearTimeout(timer);
  }, [query]);

  useEffect(() => setPage(0), [debouncedQuery, hub, utilization, haul, skin, tag, sort, pageSize]);

  const changePage = (offset: number) => {
    setPage((value) => Math.min(pages - 1, Math.max(0, value + offset)));
    window.scrollTo({ top: 0 });
  };

  useEffect(() => {
    let cancelled = false;
    const loadDailyLiveries = async (): Promise<DailyLivery[]> => {
      const stored = readDailyLiverySeed();
      if (!stored) return fetchDailyLiveries(3);
      const liveries = await fetchDailyLiveries(3, stored.seed);
      // A stored seed only stands for the day it was rolled on. Once the server
      // has moved to a new date, drop it so the day opens on its own three.
      if (liveries.length > 0 && liveries[0].day !== stored.day) {
        clearDailyLiverySeed();
        return fetchDailyLiveries(3);
      }
      return liveries;
    };
    Promise.all([fetchStats(), loadDailyLiveries()])
      .then(([nextStats, liveries]) => {
        if (!cancelled) {
          setStats(nextStats);
          setDailyLiveries(liveries);
        }
      })
      .catch(() => {
        if (!cancelled) setDailyLiveries([]);
      });
    return () => { cancelled = true; };
  }, [refreshToken]);

  // A fleet sync starts the purchase-date sweep on the server; follow it here so
  // the dates appear without a manual click, and reload the page once it lands.
  useEffect(() => {
    let cancelled = false;
    let timer = 0;
    let sawRunning = false;
    const poll = async () => {
      try {
        const status = await fetchPurchaseBackfillStatus();
        if (cancelled) return;
        setBackfill(status);
        if (status.running) {
          sawRunning = true;
          timer = window.setTimeout(poll, 3000);
        } else if (sawRunning) {
          if (status.message) setNotice(status.message);
          dataChangedRef.current();
        }
      } catch {
        /* the status endpoint is best-effort; the manual button still works */
      }
    };
    poll();
    return () => { cancelled = true; window.clearTimeout(timer); };
  }, [backfillWatch]);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    fetchFleetPage({
      q: debouncedQuery || undefined,
      hub,
      utilization,
      haul,
      tag,
      skin_filter: skin,
      sort_by: sort,
      limit: pageSize,
      offset: page * pageSize,
    }).then((data) => {
      if (cancelled) return;
      setItems(data.items);
      setTotal(data.total);
      setSelected(new Set());
    }).catch((reason) => {
      if (!cancelled) setError(reason instanceof Error ? reason.message : "Fleet request failed");
    }).finally(() => {
      if (!cancelled) setLoading(false);
    });
    return () => { cancelled = true; };
  }, [debouncedQuery, hub, utilization, haul, skin, tag, sort, page, pageSize, refreshToken]);

  const pages = Math.max(1, Math.ceil(total / pageSize));
  const activeFilters = [hub !== "all", utilization !== "all", haul !== "all", skin !== "all", tag !== "all", Boolean(debouncedQuery)].filter(Boolean).length;
  const allVisibleSelected = items.length > 0 && items.every((item) => selected.has(item.aircraft_id));
  const selectedItems = useMemo(() => items.filter((item) => selected.has(item.aircraft_id)), [items, selected]);
  // Tags shared by the whole selection: the picker ticks these so a second
  // click on one is recognisably a no-op rather than a silent re-add.
  const tagsOnEverySelected = useMemo(() => {
    if (selectedItems.length === 0) return [];
    return selectedItems[0].tags.filter((tag) => selectedItems.every(
      (item) => item.tags.some((other) => other.toLowerCase() === tag.toLowerCase())));
  }, [selectedItems]);
  const haulCounts = stats?.hauls || snapshot?.fleet_facets.hauls;
  const hubOptions = useMemo<MenuOption[]>(() => [
    { value: "all", label: "All hubs" },
    ...(stats?.hubs || snapshot?.fleet_facets.hubs || []).map((item) => ({
      value: item.hub_iata,
      label: hubLabel(item.hub_iata, item.country_code),
      hint: `${item.count} aircraft`,
    })),
  ], [stats, snapshot]);
  const tagOptions = useMemo<MenuOption[]>(() => [
    { value: "all", label: "All tags" },
    ...(stats?.tags || []).map((item) => ({
      value: item.tag,
      label: item.tag,
      hint: `${item.count} aircraft`,
    })),
  ], [stats]);
  const hubFlags = useMemo(() => {
    const map = new Map<string, string | null | undefined>();
    (stats?.hubs || snapshot?.fleet_facets.hubs || []).forEach((item) => map.set(item.hub_iata, item.country_code));
    return map;
  }, [stats, snapshot]);

  const toggleAll = () => {
    setSelected(allVisibleSelected ? new Set() : new Set(items.map((item) => item.aircraft_id)));
  };

  const toggleOne = (id: number) => {
    const next = new Set(selected);
    next.has(id) ? next.delete(id) : next.add(id);
    setSelected(next);
  };

  const clearFilters = () => {
    setQuery("");
    setDebouncedQuery("");
    setHub("all");
    setUtilization("all");
    setHaul("all");
    setSkin("all");
    setTag("all");
    setSort(DEFAULT_SORT);
  };

  const syncFleet = async () => {
    setSyncing(true);
    setNotice("Reading the fleet from the mobile connection...");
    try {
      const result = await triggerSyncFleet(hub === "all" ? undefined : hub);
      setNotice(result.message);
      onDataChanged();
      if (result.purchase_backfill) {
        setBackfill(result.purchase_backfill);
        setBackfillWatch((value) => value + 1);
      }
    } catch (reason) {
      setNotice(reason instanceof Error ? reason.message : "Fleet sync failed");
    } finally {
      setSyncing(false);
    }
  };

  const rerollDailyLiveries = async () => {
    setRerolling(true);
    try {
      const seed = Date.now().toString(36);
      const liveries = await fetchDailyLiveries(3, seed);
      setDailyLiveries(liveries);
      // Keep the seed so leaving the page and coming back -- or a plain reload
      // -- shows the rerolled three rather than the day's default pick.
      if (liveries.length > 0) writeDailyLiverySeed({ day: liveries[0].day, seed });
    } catch {
      /* keep the current pick on failure */
    } finally {
      setRerolling(false);
    }
  };

  const copySelected = async () => {
    try {
      await navigator.clipboard.writeText(selectedItems.map((item) => item.aircraft_id).join("\n"));
      setNotice(`${selectedItems.length} aircraft IDs copied.`);
    } catch {
      setNotice("Clipboard access is unavailable in this browser.");
    }
  };

  // A tag edit only changes the chips on rows that are already on screen, so it
  // patches them in place from the response instead of bumping refreshToken. A
  // full reload would re-run the fleet query, blank the grid for its loading
  // line, drop the selection, and throw the reader back to the top of the page.
  const applyTagResult = (updated: { aircraft_id: number; tags: string[] }[]) => {
    const byId = new Map(updated.map((row) => [row.aircraft_id, row.tags]));
    setItems((current) => current.map((item) => (
      byId.has(item.aircraft_id) ? { ...item, tags: byId.get(item.aircraft_id) as string[] } : item
    )));
    // Counts behind the tag filter and the picker's "Your tags" list are the
    // only other things affected; refresh them without touching the grid.
    fetchStats().then(setStats).catch(() => { /* the chips are already right */ });
  };

  const addTagToSelected = async (rawTag: string) => {
    const nextTag = rawTag.trim();
    if (!nextTag || selected.size === 0) return;
    setTagging(true);
    try {
      const result = await updateAircraftTags([...selected], { add: [nextTag] });
      applyTagResult(result.aircraft);
      setNotice(`Added “${nextTag}” to ${selected.size} aircraft.`);
    } catch (reason) {
      setNotice(reason instanceof Error ? reason.message : "Aircraft tag update failed");
    } finally {
      setTagging(false);
    }
  };

  const removeAircraftTag = async (event: React.MouseEvent, aircraftId: number, removedTag: string) => {
    event.stopPropagation();
    try {
      const result = await updateAircraftTags([aircraftId], { remove: [removedTag] });
      applyTagResult(result.aircraft);
      setNotice(`Removed “${removedTag}”.`);
    } catch (reason) {
      setNotice(reason instanceof Error ? reason.message : "Aircraft tag update failed");
    }
  };

  const rememberPurchaseDate = (aircraftId: number, purchasedAt: string) => {
    setItems((current) => current.map((item) => (
      item.aircraft_id === aircraftId ? { ...item, purchased_at: purchasedAt } : item
    )));
  };

  const summary = [
    { label: "Total fleet", value: stats?.total ?? snapshot?.portfolio.fleet_total, note: "aircraft", tone: "" },
    { label: "Active", value: stats?.active ?? snapshot?.portfolio.active, note: "scheduled", tone: "green" },
    { label: "Warehouse", value: stats?.idle ?? snapshot?.portfolio.idle, note: "idle", tone: "amber" },
    { label: "Avg utilization", value: stats ? `${stats.avg_utilization}%` : snapshot ? `${snapshot.portfolio.avg_utilization}%` : undefined, note: "fleet average", tone: "cyan" },
    { label: "Special liveries", value: stats?.special_skin_count, note: "aircraft", tone: "violet" },
  ];

  return (
    <div className="fleet-workspace" ref={workspaceRef}>
      <section className="fleet-summary" aria-label="Fleet summary">
        {summary.map((item) => (
          <article className={`fleet-stat${item.tone ? ` tone-${item.tone}` : ""}`} key={item.label}>
            <span>{item.label}</span>
            <div><strong>{item.value === undefined ? "..." : typeof item.value === "number" ? integer.format(item.value) : item.value}</strong><small>{item.note}</small></div>
          </article>
        ))}
      </section>

      <section className="daily-liveries">
        <div className="fleet-section-heading">
          <div><h2><Sparkles size={16} /> Showcase</h2><p className="section-subtitle">Liveries of the day</p></div>
          <button className="reroll-button" onClick={rerollDailyLiveries} disabled={rerolling} title="Reroll today's liveries">
            <Dices size={16} className={rerolling ? "is-spinning" : ""} />
            <span>Reroll</span>
          </button>
        </div>
        <div className="daily-livery-grid">
          {dailyLiveries.length === 0 ? [0, 1, 2].map((index) => <div className="daily-livery-card is-placeholder" key={index} />) : dailyLiveries.map((livery) => {
            // The same livery usually flies from several bases, so the card
            // names each of them (with how many planes sit there) instead of
            // implying the whole batch lives at the sample aircraft's hub.
            const hubs = livery.hubs?.length
              ? livery.hubs
              : livery.sample_aircraft?.hub_iata
                ? [{ hub_iata: livery.sample_aircraft.hub_iata, count: livery.fleet_count }]
                : [];
            const hiddenHubs = hubs.slice(DAILY_HUB_CHIPS);
            return (
              <article className="daily-livery-card is-clickable" key={livery.skin_id} onClick={() => onOpenLivery(livery.skin_id)} title="Open this exact livery in the Liveries tab">
                <AircraftArtwork skinId={livery.skin_id} alt={livery.name} featured />
                <div className="daily-livery-copy">
                  <strong>{cleanLiveryName(livery.name)}</strong>
                  <small>{livery.sample_aircraft?.model || splitLiveryName(livery.name).model}</small>
                  <div>
                    <b>{livery.fleet_count} in fleet</b>
                    {hubs.slice(0, DAILY_HUB_CHIPS).map((hub) => (
                      <b key={hub.hub_iata} title={`${hub.count} at ${hubLabel(hub.hub_iata, hubFlags.get(hub.hub_iata))}`}>
                        {hubLabel(hub.hub_iata, hubFlags.get(hub.hub_iata))}
                        {hubs.length > 1 && <i>{hub.count}</i>}
                      </b>
                    ))}
                    {hiddenHubs.length > 0 && (
                      <b title={hiddenHubs.map((hub) => `${hubLabel(hub.hub_iata, hubFlags.get(hub.hub_iata))} (${hub.count})`).join(", ")}>+{hiddenHubs.length}</b>
                    )}
                  </div>
                </div>
              </article>
            );
          })}
        </div>
      </section>

      <nav className="haul-tabs" aria-label="Aircraft haul type">
        {HAUL_TABS.map((tab) => (
          <button className={haul === tab.key ? "is-active" : ""} key={tab.key} onClick={() => setHaul(tab.key)}>
            {tab.key === "cargo" && <Package size={15} />}
            <span>{tab.label}</span>
            <small>{integer.format(haulCounts?.[tab.key] || 0)}</small>
          </button>
        ))}
      </nav>

      <section className="fleet-controls">
        <div className="fleet-toolbar">
          <label className="search-control">
            <Search size={17} />
            <input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search aircraft, model, ICAO, hub, or livery" />
            {query && <button onClick={() => setQuery("")} aria-label="Clear search"><X size={15} /></button>}
          </label>
          <MenuSelect label="Hub" value={hub} onChange={setHub} options={hubOptions} icon={Building2} />
          <MenuSelect label="Status" value={utilization} onChange={(next) => setUtilization(next as UtilizationFilter)} options={STATUS_OPTIONS} icon={Gauge} />
          <MenuSelect label="Livery" value={skin} onChange={(next) => setSkin(next as typeof skin)} options={LIVERY_OPTIONS} icon={Layers} />
          <MenuSelect label="Tag" value={tag} onChange={setTag} options={tagOptions} icon={Tag} />
          <MenuSelect label="Sort by" value={sort} onChange={setSort} groups={SORT_GROUPS} />
          <div className="view-switch" aria-label="Fleet display mode">
            <button className={viewMode === "grid" ? "is-active" : ""} onClick={() => setViewMode("grid")} title="Tile view"><LayoutGrid size={16} /></button>
            <button className={viewMode === "list" ? "is-active" : ""} onClick={() => setViewMode("list")} title="List view"><List size={17} /></button>
          </div>
          <button className="primary-action" onClick={syncFleet} disabled={syncing}>
            <RefreshCcw size={16} className={syncing ? "is-spinning" : ""} />
            {syncing ? "Syncing" : hub === "all" ? "Sync fleet" : `Sync ${hub}`}
          </button>
        </div>

        <div className="active-filter-row">
          <span><Filter size={14} /> {activeFilters ? `${activeFilters} active filter${activeFilters === 1 ? "" : "s"}` : "No filters applied"}</span>
          {activeFilters > 0 && <button onClick={clearFilters}>Clear all</button>}
          <small>Showing {total ? page * pageSize + 1 : 0} to {Math.min((page + 1) * pageSize, total)} of {integer.format(total)}</small>
          {snapshot?.status.fleet_last_synced && <small>Last synced {formatTime(snapshot.status.fleet_last_synced)}</small>}
          {backfill?.running && (
            <small className="backfill-progress">
              <CalendarDays size={12} /> Purchase dates {integer.format(backfill.done + backfill.failed)}
              {backfill.total ? ` / ${integer.format(backfill.total)}` : ""}
              {backfill.eta_minutes ? ` (~${integer.format(backfill.eta_minutes)} min left)` : ""}
            </small>
          )}
        </div>
      </section>

      {notice && <div className="inline-notice"><span>{notice}</span><button onClick={() => setNotice(null)} aria-label="Dismiss notice"><X size={14} /></button></div>}

      {loading && items.length === 0 ? (
        <div className="fleet-loading">Loading fleet data...</div>
      ) : error ? (
        <div className="table-error">{error}</div>
      ) : items.length === 0 ? (
        <div className="table-empty"><SlidersHorizontal size={22} /><strong>No aircraft match</strong><span>Adjust or clear the active filters.</span></div>
      ) : viewMode === "grid" ? (
        <div className={`aircraft-grid${loading ? " is-refetching" : ""}`}>
          {items.map((item) => {
            const badge = liveryBadge(item);
            const selectedItem = selected.has(item.aircraft_id);
            return (
              <article className={`aircraft-card${selectedItem ? " is-selected" : ""}`} key={item.aircraft_id}
                role="link" tabIndex={0} onClick={() => onOpenAircraft(item.aircraft_id)}
                onKeyDown={(event) => { if (event.target === event.currentTarget && event.key === "Enter") onOpenAircraft(item.aircraft_id); }}>
                <div className="aircraft-card-head">
                  <button className={`check-button${selectedItem ? " is-checked" : ""}`} aria-label={`Select ${item.name}`}
                    onClick={(event) => { event.stopPropagation(); toggleOne(item.aircraft_id); }}>{selectedItem && <Check size={13} />}</button>
                  <div><strong>{cleanAircraftName(item.name) || `Aircraft ${item.aircraft_id}`}</strong><small>#{item.aircraft_id}</small></div>
                  <span className="hub-code">{item.hub_iata ? hubLabel(item.hub_iata, hubFlags.get(item.hub_iata)) : "?"}</span>
                </div>
                <AircraftArtwork skinId={item.skin_id} alt={`${item.model} ${cleanLiveryName(item.skin_name)}`} />
                <div className="aircraft-card-model"><div><strong>{item.model}</strong><small>{item.icao_code || haulLabel(item)}</small></div><PurchaseDate aircraft={item} backfilling={backfill?.running} onLoaded={rememberPurchaseDate} onError={setNotice} /></div>
                <div className={`util-cell ${item.utilization === 0 ? "is-idle" : item.utilization < 100 ? "is-partial" : "is-full"}`}><span><i style={{ width: `${Math.min(100, item.utilization)}%` }} /></span><strong>{Math.round(item.utilization)}%</strong></div>
                <AircraftConfig aircraft={item} />
                {item.tags.length > 0 && <div className="aircraft-tags">{item.tags.map((itemTag) => <span className="aircraft-tag" key={itemTag}><Tag size={10} /><b>{itemTag}</b><button onClick={(event) => removeAircraftTag(event, item.aircraft_id, itemTag)} aria-label={`Remove ${itemTag} from ${item.name}`}><X size={10} /></button></span>)}</div>}
                <div className="aircraft-card-foot"><span className={`livery-chip tone-${badge.tone}`}>{badge.label}</span><span className="haul-chip">{haulLabel(item)}</span></div>
              </article>
            );
          })}
        </div>
      ) : (
        <div className={`fleet-table-shell${loading ? " is-refetching" : ""}`}>
          <table className="fleet-table">
            <thead><tr><th className="check-column"><button className={`check-button${allVisibleSelected ? " is-checked" : ""}`} onClick={toggleAll} aria-label="Select visible aircraft">{allVisibleSelected && <Check size={13} />}</button></th><th>Aircraft</th><th>Model</th><th>Hub</th><th>Utilization</th><th>Configuration</th><th>Livery</th></tr></thead>
            <tbody>{items.map((item) => {
              const badge = liveryBadge(item);
              return <tr key={item.aircraft_id} className={selected.has(item.aircraft_id) ? "is-selected" : ""}
                role="link" tabIndex={0} onClick={() => onOpenAircraft(item.aircraft_id)}
                onKeyDown={(event) => { if (event.target === event.currentTarget && event.key === "Enter") onOpenAircraft(item.aircraft_id); }}>
                <td className="check-column"><button className={`check-button${selected.has(item.aircraft_id) ? " is-checked" : ""}`} aria-label={`Select ${item.name}`}
                  onClick={(event) => { event.stopPropagation(); toggleOne(item.aircraft_id); }}>{selected.has(item.aircraft_id) && <Check size={13} />}</button></td>
                <td><div className="aircraft-list-name"><AircraftArtwork skinId={item.skin_id} alt="" /><span><strong>{cleanAircraftName(item.name) || `Aircraft ${item.aircraft_id}`}</strong><small>#{item.aircraft_id}</small>{item.tags.length > 0 && <span className="aircraft-tags">{item.tags.map((itemTag) => <span className="aircraft-tag" key={itemTag}><Tag size={10} /><b>{itemTag}</b><button onClick={(event) => removeAircraftTag(event, item.aircraft_id, itemTag)} aria-label={`Remove ${itemTag} from ${item.name}`}><X size={10} /></button></span>)}</span>}<PurchaseDate aircraft={item} backfilling={backfill?.running} onLoaded={rememberPurchaseDate} onError={setNotice} /></span></div></td>
                <td><strong>{item.model}</strong><small>{item.icao_code || haulLabel(item)}</small></td>
                <td><span className="hub-code">{item.hub_iata ? hubLabel(item.hub_iata, hubFlags.get(item.hub_iata)) : "?"}</span></td>
                <td><div className={`util-cell ${item.utilization === 0 ? "is-idle" : item.utilization < 100 ? "is-partial" : "is-full"}`}><span><i style={{ width: `${Math.min(100, item.utilization)}%` }} /></span><strong>{Math.round(item.utilization)}%</strong></div></td>
                <td><AircraftConfig aircraft={item} /></td>
                <td><span className={`livery-chip tone-${badge.tone}`}>{badge.label}</span><small>{haulLabel(item)}</small></td>
              </tr>;
            })}</tbody>
          </table>
        </div>
      )}

      <div className="fleet-footer">
        <span>Page {page + 1} of {pages}</span>
        <div><button onClick={() => changePage(-1)} disabled={page === 0}><ChevronLeft size={16} /> Previous</button><button onClick={() => changePage(1)} disabled={page + 1 >= pages}>Next <ChevronRight size={16} /></button></div>
      </div>

      {selected.size > 0 && <div className="selection-bar"><strong>{selected.size} selected</strong><TagPicker known={stats?.tags || []} applied={tagsOnEverySelected} onApply={addTagToSelected} busy={tagging} /><button onClick={copySelected}><Copy size={15} /> Copy IDs</button><button onClick={() => setSelected(new Set())}>Clear</button></div>}
    </div>
  );
}
