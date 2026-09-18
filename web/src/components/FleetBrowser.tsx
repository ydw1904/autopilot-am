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
  Gauge,
  Layers,
  LayoutGrid,
  List,
  Plane,
  Tag,
  X,
} from "lucide-react";
import { fetchAircraftPurchaseDate, fetchFleetPage, fetchStats, updateAircraftTags } from "../api";
import type { PurchaseBackfillStatus } from "../api";
import { FleetAircraft, FleetStats, HaulTab } from "../types";
import { integer, shortDate } from "../format";
import { hubFlagMap, hubLabel } from "../hubFlag";
import { cleanLiveryName } from "../liveryName";
import { FilterBar, SearchInput, useDebouncedQuery } from "./FilterBar";
import { MenuOption, MenuSelect } from "./MenuSelect";
import { EmptyState, ErrorState, LoadingState } from "./PageStates";
import { SegmentedControl } from "./SegmentedControl";
import { TagPicker } from "./TagPicker";
import { Button } from "@/components/ui/button";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";

export type UtilizationFilter = "all" | "active" | "idle" | "partial" | "full";
type ViewMode = "grid" | "list";

const GRID_CARD_MIN_WIDTH = 275;
const GRID_GAP = 12;
const GRID_ROWS_PER_PAGE = 8;

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
        <CalendarDays size={11} /> Purchased {shortDate(aircraft.purchased_at)}
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
    <Button className="purchase-date is-action" onClick={load} disabled={loading} title="Fetch and cache the exact purchase date">
      <CalendarDays size={11} /> {loading ? "Loading date..." : "Load purchase date"}
    </Button>
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

export function AircraftArtwork({ skinId, alt, featured = false }: { skinId: number | null; alt: string; featured?: boolean }) {
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

export interface FleetBrowserProps {
  refreshToken: number;
  /** Filters every page request is pinned to, on top of the reader's own. */
  scope?: { name_query?: string; haul?: HaulTab };
  /** "idle" | "livery:<name>" | "name:<name>" -- opens with that filter applied. */
  preset?: string;
  /** Hub menu, rendered only when the owner tracks the hub (the fleet tab's sync uses it). */
  hub?: string;
  hubOptions?: MenuOption[];
  onHubChange?: (hub: string) => void;
  searchPlaceholder?: string;
  /** Extra toolbar controls (the fleet tab's sync button) and filter-row notes. */
  toolbarExtra?: React.ReactNode;
  filterNotes?: React.ReactNode;
  backfill?: PurchaseBackfillStatus | null;
  notice?: string | null;
  onNotice: (message: string | null) => void;
  onOpenAircraft: (aircraftId: number) => void;
  /** The facets this browser loads anyway, lifted for the owner's own header. */
  onStats?: (stats: FleetStats) => void;
  className?: string;
}

export function FleetBrowser({
  refreshToken, scope, preset, hub, hubOptions, onHubChange, searchPlaceholder,
  toolbarExtra, filterNotes, backfill, notice, onNotice, onOpenAircraft,
  onStats, className,
}: FleetBrowserProps) {
  const rootRef = useRef<HTMLDivElement>(null);
  const [items, setItems] = useState<FleetAircraft[]>([]);
  const [total, setTotal] = useState(0);
  const [stats, setStats] = useState<FleetStats | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const debouncedQuery = useDebouncedQuery(query);
  const [utilization, setUtilization] = useState<UtilizationFilter>(preset === "idle" ? "idle" : "all");
  const [skin, setSkin] = useState<"all" | "special" | "manufacturer">("all");
  const [tag, setTag] = useState("all");
  const [sort, setSort] = useState(DEFAULT_SORT);
  const [viewMode, setViewMode] = useState<ViewMode>("grid");
  const [page, setPage] = useState(0);
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [tagging, setTagging] = useState(false);
  const [gridColumns, setGridColumns] = useState(1);
  const pageSize = gridColumns * GRID_ROWS_PER_PAGE;
  // The parent re-creates onStats every render; the fetch loop must not
  // restart because of that.
  const onStatsRef = useRef(onStats);
  onStatsRef.current = onStats;

  useEffect(() => {
    const root = rootRef.current;
    if (!root) return;
    // Hidden behind the aircraft editor the width reads 0; ignore it, or the
    // page size would shrink and reset the reader's page.
    const measure = () => root.clientWidth && setGridColumns(Math.max(1, Math.floor((root.clientWidth + GRID_GAP) / (GRID_CARD_MIN_WIDTH + GRID_GAP))));
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(root);
    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    if (!preset) return;
    if (preset === "idle") setUtilization("idle");
    else if (preset.startsWith("livery:")) setQuery(preset.slice("livery:".length));
    else if (preset.startsWith("name:")) setQuery(preset.slice("name:".length));
  }, [preset]);

  const refreshStats = () => fetchStats().then((next) => { setStats(next); onStatsRef.current?.(next); });

  useEffect(() => { refreshStats().catch(() => { /* the filters fall back to no facets */ }); }, [refreshToken]);

  useEffect(() => setPage(0), [debouncedQuery, hub, utilization, scope?.haul, scope?.name_query, skin, tag, sort, pageSize]);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    fetchFleetPage({
      q: debouncedQuery || undefined,
      name_query: scope?.name_query,
      hub: hub || "all",
      utilization,
      haul: scope?.haul,
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
  }, [debouncedQuery, hub, utilization, scope?.haul, scope?.name_query, skin, tag, sort, page, pageSize, refreshToken]);

  const pages = Math.max(1, Math.ceil(total / pageSize));
  const activeFilters = [hub && hub !== "all", utilization !== "all", skin !== "all", tag !== "all", Boolean(debouncedQuery)].filter(Boolean).length;
  const allVisibleSelected = items.length > 0 && items.every((item) => selected.has(item.aircraft_id));
  const selectedItems = useMemo(() => items.filter((item) => selected.has(item.aircraft_id)), [items, selected]);
  // Tags shared by the whole selection: the picker ticks these so a second
  // click on one is recognisably a no-op rather than a silent re-add.
  const tagsOnEverySelected = useMemo(() => {
    if (selectedItems.length === 0) return [];
    return selectedItems[0].tags.filter((tag) => selectedItems.every(
      (item) => item.tags.some((other) => other.toLowerCase() === tag.toLowerCase())));
  }, [selectedItems]);
  const tagOptions = useMemo<MenuOption[]>(() => [
    { value: "all", label: "All tags" },
    ...(stats?.tags || []).map((item) => ({ value: item.tag, label: item.tag, hint: `${item.count} aircraft` })),
  ], [stats]);
  const hubFlags = useMemo(() => hubFlagMap(stats?.hubs), [stats]);

  const changePage = (offset: number) => {
    setPage((value) => Math.min(pages - 1, Math.max(0, value + offset)));
    window.scrollTo({ top: 0 });
  };

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
    onHubChange?.("all");
    setUtilization("all");
    setSkin("all");
    setTag("all");
    setSort(DEFAULT_SORT);
  };

  const copySelected = async () => {
    try {
      await navigator.clipboard.writeText(selectedItems.map((item) => item.aircraft_id).join("\n"));
      onNotice(`${selectedItems.length} aircraft IDs copied.`);
    } catch {
      onNotice("Clipboard access is unavailable in this browser.");
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
    refreshStats().catch(() => { /* the chips are already right */ });
  };

  const addTagToSelected = async (rawTag: string) => {
    const nextTag = rawTag.trim();
    if (!nextTag || selected.size === 0) return;
    setTagging(true);
    try {
      const result = await updateAircraftTags([...selected], { add: [nextTag] });
      applyTagResult(result.aircraft);
      onNotice(`Added “${nextTag}” to ${selected.size} aircraft.`);
    } catch (reason) {
      onNotice(reason instanceof Error ? reason.message : "Aircraft tag update failed");
    } finally {
      setTagging(false);
    }
  };

  const removeAircraftTag = async (event: React.MouseEvent, aircraftId: number, removedTag: string) => {
    event.stopPropagation();
    try {
      const result = await updateAircraftTags([aircraftId], { remove: [removedTag] });
      applyTagResult(result.aircraft);
      onNotice(`Removed “${removedTag}”.`);
    } catch (reason) {
      onNotice(reason instanceof Error ? reason.message : "Aircraft tag update failed");
    }
  };

  const rememberPurchaseDate = (aircraftId: number, purchasedAt: string) => {
    setItems((current) => current.map((item) => (
      item.aircraft_id === aircraftId ? { ...item, purchased_at: purchasedAt } : item
    )));
  };

  const aircraftTags = (item: FleetAircraft) => (
    <span className="aircraft-tags">{item.tags.map((itemTag) => (
      <span className="aircraft-tag" key={itemTag}><Tag size={10} /><b>{itemTag}</b>
        <Button onClick={(event) => removeAircraftTag(event, item.aircraft_id, itemTag)} aria-label={`Remove ${itemTag} from ${item.name}`}><X size={10} /></Button>
      </span>
    ))}</span>
  );

  return (
    <div className={`fleet-browser${className ? ` ${className}` : ""}`} ref={rootRef}>
      <FilterBar active={activeFilters} onClear={clearFilters} status={<>
        <small>Showing {total ? page * pageSize + 1 : 0} to {Math.min((page + 1) * pageSize, total)} of {integer.format(total)}</small>
        {filterNotes}
        {backfill?.running && (
          <small className="backfill-progress">
            <CalendarDays size={12} /> Purchase dates {integer.format(backfill.done + backfill.failed)}
            {backfill.total ? ` / ${integer.format(backfill.total)}` : ""}
            {backfill.eta_minutes ? ` (~${integer.format(backfill.eta_minutes)} min left)` : ""}
          </small>
        )}
      </>}>
          <SearchInput value={query} onChange={setQuery} placeholder={searchPlaceholder || "Search aircraft, model, ICAO, hub, or livery"} />
          {hubOptions && onHubChange && <MenuSelect label="Hub" value={hub || "all"} onChange={onHubChange} options={hubOptions} icon={Building2} />}
          <MenuSelect label="Status" value={utilization} onChange={(next) => setUtilization(next as UtilizationFilter)} options={STATUS_OPTIONS} icon={Gauge} />
          <MenuSelect label="Livery" value={skin} onChange={(next) => setSkin(next as typeof skin)} options={LIVERY_OPTIONS} icon={Layers} />
          <MenuSelect label="Tag" value={tag} onChange={setTag} options={tagOptions} icon={Tag} />
          <MenuSelect label="Sort by" value={sort} onChange={setSort} groups={SORT_GROUPS} />
          <SegmentedControl className="view-switch" label="Fleet display mode" value={viewMode} onChange={setViewMode} options={[
            { value: "grid", label: <LayoutGrid size={16} />, title: "Tile view" },
            { value: "list", label: <List size={17} />, title: "List view" },
          ]} />
          {toolbarExtra}
      </FilterBar>

      {notice && <div className="inline-notice"><span>{notice}</span><Button onClick={() => onNotice(null)} aria-label="Dismiss notice"><X size={14} /></Button></div>}

      {loading && items.length === 0 ? (
        <LoadingState />
      ) : error ? (
        <ErrorState inline title="Fleet unavailable" message={error} />
      ) : items.length === 0 ? (
        <EmptyState title="No aircraft match" hint="Adjust or clear the active filters." />
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
                  <Button className={`check-button${selectedItem ? " is-checked" : ""}`} aria-label={`Select ${item.name}`}
                    onClick={(event) => { event.stopPropagation(); toggleOne(item.aircraft_id); }}>{selectedItem && <Check size={13} />}</Button>
                  <div><strong>{cleanAircraftName(item.name) || `Aircraft ${item.aircraft_id}`}</strong><small>#{item.aircraft_id}</small></div>
                  <span className="hub-code">{item.hub_iata ? hubLabel(item.hub_iata, hubFlags.get(item.hub_iata)) : "?"}</span>
                </div>
                <AircraftArtwork skinId={item.skin_id} alt={`${item.model} ${cleanLiveryName(item.skin_name)}`} />
                <div className="aircraft-card-model"><div><strong>{item.model}</strong><small>{item.icao_code || haulLabel(item)}</small></div><PurchaseDate aircraft={item} backfilling={backfill?.running} onLoaded={rememberPurchaseDate} onError={onNotice} /></div>
                <div className={`util-cell ${item.utilization === 0 ? "is-idle" : item.utilization <= 93 ? "is-partial" : "is-full"}`}><span><i style={{ width: `${Math.min(100, item.utilization)}%` }} /></span><strong>{Math.round(item.utilization)}%</strong></div>
                <AircraftConfig aircraft={item} />
                {item.tags.length > 0 && <div className="aircraft-tags">{aircraftTags(item)}</div>}
                <div className="aircraft-card-foot"><span className={`livery-chip tone-${badge.tone}`}>{badge.label}</span><span className="haul-chip">{haulLabel(item)}</span></div>
              </article>
            );
          })}
        </div>
      ) : (
        <div className={`fleet-table-shell${loading ? " is-refetching" : ""}`}>
          <Table className="fleet-table">
            <TableHeader><TableRow><TableHead className="check-column"><Button className={`check-button${allVisibleSelected ? " is-checked" : ""}`} onClick={toggleAll} aria-label="Select visible aircraft">{allVisibleSelected && <Check size={13} />}</Button></TableHead><TableHead>Aircraft</TableHead><TableHead>Model</TableHead><TableHead>Hub</TableHead><TableHead>Utilization</TableHead><TableHead>Configuration</TableHead><TableHead>Livery</TableHead></TableRow></TableHeader>
            <TableBody>{items.map((item) => {
              const badge = liveryBadge(item);
              return <TableRow key={item.aircraft_id} className={selected.has(item.aircraft_id) ? "is-selected" : ""}
                role="link" tabIndex={0} onClick={() => onOpenAircraft(item.aircraft_id)}
                onKeyDown={(event) => { if (event.target === event.currentTarget && event.key === "Enter") onOpenAircraft(item.aircraft_id); }}>
                <TableCell className="check-column"><Button className={`check-button${selected.has(item.aircraft_id) ? " is-checked" : ""}`} aria-label={`Select ${item.name}`}
                  onClick={(event) => { event.stopPropagation(); toggleOne(item.aircraft_id); }}>{selected.has(item.aircraft_id) && <Check size={13} />}</Button></TableCell>
                <TableCell><div className="aircraft-list-name"><AircraftArtwork skinId={item.skin_id} alt="" /><span><strong>{cleanAircraftName(item.name) || `Aircraft ${item.aircraft_id}`}</strong><small>#{item.aircraft_id}</small>{item.tags.length > 0 && aircraftTags(item)}<PurchaseDate aircraft={item} backfilling={backfill?.running} onLoaded={rememberPurchaseDate} onError={onNotice} /></span></div></TableCell>
                <TableCell><strong>{item.model}</strong><small>{item.icao_code || haulLabel(item)}</small></TableCell>
                <TableCell><span className="hub-code">{item.hub_iata ? hubLabel(item.hub_iata, hubFlags.get(item.hub_iata)) : "?"}</span></TableCell>
                <TableCell><div className={`util-cell ${item.utilization === 0 ? "is-idle" : item.utilization <= 93 ? "is-partial" : "is-full"}`}><span><i style={{ width: `${Math.min(100, item.utilization)}%` }} /></span><strong>{Math.round(item.utilization)}%</strong></div></TableCell>
                <TableCell><AircraftConfig aircraft={item} /></TableCell>
                <TableCell><span className={`livery-chip tone-${badge.tone}`}>{badge.label}</span><small>{haulLabel(item)}</small></TableCell>
              </TableRow>;
            })}</TableBody>
          </Table>
        </div>
      )}

      <div className="fleet-footer">
        <span>Page {page + 1} of {pages}</span>
        <div><Button onClick={() => changePage(-1)} disabled={page === 0}><ChevronLeft size={16} /> Previous</Button><Button onClick={() => changePage(1)} disabled={page + 1 >= pages}>Next <ChevronRight size={16} /></Button></div>
      </div>

      {selected.size > 0 && <div className="selection-bar"><strong>{selected.size} selected</strong><TagPicker known={stats?.tags || []} applied={tagsOnEverySelected} onApply={addTagToSelected} busy={tagging} /><Button onClick={copySelected}><Copy size={15} /> Copy IDs</Button><Button onClick={() => setSelected(new Set())}>Clear</Button></div>}
    </div>
  );
}
