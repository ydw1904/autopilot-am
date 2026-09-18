import { useEffect, useState } from "react";
import {
  ColumnDef,
  flexRender,
  getCoreRowModel,
  useReactTable,
} from "@tanstack/react-table";
import {
  Activity,
  ArrowDown,
  ArrowDownWideNarrow,
  ArrowUp,
  ArrowUpNarrowWide,
  ChevronsUpDown,
  Clock3,
  Coins,
  Eye,
  Filter,
  Gavel,
  Gift,
  Hand,
  Package,
  Plane,
  Radar,
  ShieldCheck,
  ShoppingBag,
  Ticket,
  Trophy,
} from "lucide-react";
import { fetchShmMonitor, updateShmWatch } from "../api";
import { EMPTY, integer, parseGameDate, shortMoney } from "../format";
import { ShmMonitorSnapshot, ShmWatch } from "../types";
import { FilterBar, SearchInput } from "./FilterBar";
import { MenuOption, MenuSelect } from "./MenuSelect";
import { EmptyState, ErrorState, LoadingState } from "./PageStates";
import { SectionHeader } from "./SectionHeader";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import {
  DECISION_SORTS,
  FeedFilters,
  NO_FEED_FILTERS,
  NO_WATCH_FILTERS,
  SIGHTING_SORTS,
  STATE_LABELS,
  SortDir,
  SortSpec,
  WATCH_COLUMNS,
  WATCH_SORTS,
  WatchFilters,
  checkSorts,
  countFeedFilters,
  countWatchFilters,
  filterChecks,
  filterDecisions,
  filterSightings,
  filterWatches,
  groupDecisions,
  isLiveWatch,
  modelNames,
  nextSort,
  sortBy,
  sortByValue,
  sourceBucket,
  sourceGroups,
  sourceStyle,
  watchLivery,
  watchModel,
  watchState,
} from "./shmFilters";

// Per-cell attributes the watch table needs (numeric alignment, the
// price-reach colour, empty-state dimming) ride on the column's meta so the
// body renderer stays one generic flexRender loop.
declare module "@tanstack/react-table" {
  // eslint-disable-next-line @typescript-eslint/no-unused-vars
  interface ColumnMeta<TData, TValue> {
    className?: (row: TData) => string | undefined;
    title?: (row: TData) => string | undefined;
  }
}


const SOURCE_ICONS: Record<string, typeof Trophy> = {
  "is-shop-pack": ShoppingBag,
  "is-shop-tc": Ticket,
  "is-shop-amc": Coins,
  "is-shop-gift": Gift,
  "is-challenge": Trophy,
  "is-booster": Package,
  "is-manual": Hand,
};

const OWNERSHIP_OPTIONS: MenuOption[] = [
  { value: "all", label: "All ownership" },
  { value: "missing", label: "Missing", hint: "not in hangar" },
  { value: "owned", label: "Owned" },
];
const STATE_OPTIONS: MenuOption[] = [
  { value: "all", label: "All watches" },
  { value: "armed", label: "Armed", hint: "will buy" },
  { value: "observing", label: "Observing", hint: "watch only" },
  { value: "inactive", label: "Dormant", hint: "acquired or retired" },
];
const SIGHTING_FILTER_OPTIONS: MenuOption[] = [
  { value: "all", label: "All listings" },
  { value: "ending", label: "Ending soon", hint: "under an hour left" },
  { value: "bids", label: "Has bids", hint: "someone else is in" },
];
const CHECK_FILTER_OPTIONS: MenuOption[] = [
  { value: "all", label: "All checks" },
  { value: "truncated", label: "Truncated only" },
  { value: "complete", label: "Complete only" },
];
const DECISION_FILTER_OPTIONS: MenuOption[] = [
  { value: "all", label: "All decisions" },
  { value: "dry_run", label: "Dry run only" },
  { value: "live", label: "Live only" },
];

/** Every feed sort is one menu of "field, direction" pairs, built from the same
 *  specs the watch table sorts by. The spec's own direction leads (a listing
 *  feed opens newest first), then its opposite, and each names itself in a hint
 *  the way the fleet and livery sort menus do. */
function sortOptions<T>(specs: Record<string, SortSpec<T>>): MenuOption[] {
  return Object.entries(specs).flatMap(([key, spec]) =>
    ([spec.dir, -spec.dir as SortDir]).map((dir) => ({
      value: `${key}:${dir}`,
      label: spec.label,
      hint: spec.hints[dir === 1 ? 0 : 1],
      icon: dir === 1 ? ArrowUpNarrowWide : ArrowDownWideNarrow,
    })));
}

/** The filter shell every feed shares, and the same one the watched-liveries
 *  table already uses: search, the feed's own dropdown, its sort, and the
 *  standard "N active filters / showing X of Y" strip underneath. */
function FeedControls<T>({ filters, onChange, options, placeholder, sort, onSort, sortSpecs, shown, total }: {
  filters: FeedFilters;
  onChange: (filters: FeedFilters) => void;
  options: MenuOption[];
  placeholder: string;
  sort: string;
  onSort: (sort: string) => void;
  sortSpecs: Record<string, SortSpec<T>>;
  shown: number;
  total: number;
}) {
  return (
    <FilterBar className="shm-controls is-feed" active={countFeedFilters(filters)} onClear={() => onChange(NO_FEED_FILTERS)}
      status={<small>Showing {integer.format(shown)} of {integer.format(total)}</small>}>
      <SearchInput value={filters.query} onChange={(query) => onChange({ ...filters, query })} placeholder={placeholder} />
      <MenuSelect label="Filter" value={filters.select} onChange={(select) => onChange({ ...filters, select })} options={options} icon={Filter} />
      <MenuSelect label="Sort by" value={sort} onChange={onSort} options={sortOptions(sortSpecs)} align="right" />
    </FilterBar>
  );
}

// Accepts what shortMoney() prints back ("$2.00B", "$1,250"), plus the shorthand the
// CLI takes ("2b", "850m"). Empty clears the cap; anything else is rejected so a
// typo never silently becomes a $0 cap that buys nothing.
function parseMoney(raw: string): number | null | undefined {
  const text = raw.trim().toLowerCase().replace(/[$,_\s]/g, "");
  if (!text) return null;
  const match = /^(\d+(?:\.\d+)?)([kmb]?)$/.exec(text);
  if (!match) return undefined;
  const scale = { "": 1, k: 1e3, m: 1e6, b: 1e9 }[match[2]] ?? 1;
  return Math.round(Number(match[1]) * scale);
}

function relativeTime(value: string | null | undefined): string {
  const date = parseGameDate(value);
  if (!date) return "Never";
  const seconds = Math.max(0, Math.floor((Date.now() - date.getTime()) / 1000));
  if (seconds < 60) return `${seconds}s ago`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`;
  if (seconds < 86_400) return `${Math.floor(seconds / 3600)}h ago`;
  return date.toLocaleDateString();
}

function duration(seconds: number | null): string {
  if (seconds === null) return "Unknown";
  if (seconds < 3600) return `${Math.max(0, Math.floor(seconds / 60))}m`;
  return `${Math.floor(seconds / 3600)}h ${Math.floor((seconds % 3600) / 60)}m`;
}

/** The cached livery PNG, at the one size every part of this tab uses it.
 *  Not every skin has one cached, so it falls back the way the fleet tiles do
 *  rather than leaving a browser's broken-image glyph in the row. */
function Thumb({ skinId, alt }: { skinId: number; alt: string }) {
  const [failed, setFailed] = useState(false);
  return (
    <span className="shm-thumb">
      {failed
        ? <Plane size={16} />
        : <img src={`/api/skin_image/${skinId}`} alt={alt} loading="lazy" onError={() => setFailed(true)} />}
    </span>
  );
}

function SourceChip({ source }: { source: string | null }) {
  const style = sourceStyle(source);
  const Icon = SOURCE_ICONS[style.cls];
  return (
    <span className={`livery-tag ${style.cls}`} title={style.hint ? `${style.label} — ${style.hint}` : style.label}>
      <Icon size={10} /> {style.label}
    </span>
  );
}

/** Whether the cheapest listing ever seen would clear the row's own price cap:
 *  the watcher buys at the listing's BIN, so this is the difference between a
 *  standing order that can fire and one that never will. */
function capReach(watch: ShmWatch): { cls: string; title: string } {
  if (watch.cheapest_seen === null) return { cls: "", title: "This livery has not been listed since the watch was added" };
  if (watch.max_price === null) return { cls: "", title: "No cap set — any buy-now price is accepted" };
  return watch.cheapest_seen <= watch.max_price
    ? { cls: "is-under-cap", title: `Within the ${shortMoney(watch.max_price)} cap` }
    : { cls: "is-over-cap", title: `Above the ${shortMoney(watch.max_price)} cap — this watch cannot fire at that price` };
}

/** Why a weighed listing was left alone. The watcher takes a listing only at
 *  its own buy-now price, and only from a live, armed watch under its cap — so
 *  the first of those that fails is the whole story of the row. */
function decisionGuard(binPrice: number, watch: ShmWatch | undefined): { value: string; label: string; blocked: boolean } {
  if (!watch) return { value: EMPTY, label: "no longer watched", blocked: false };
  if (watch.max_price !== null && binPrice > watch.max_price) {
    return { value: shortMoney(watch.max_price), label: "over cap", blocked: true };
  }
  if (!isLiveWatch(watch)) return { value: STATE_LABELS[watchState(watch)].label, label: "dormant", blocked: true };
  if (!watch.armed) return { value: "Observe", label: "not armed", blocked: true };
  return { value: shortMoney(watch.max_price), label: "price cap", blocked: false };
}

/** The watched-liveries grid. A TanStack table owns the column model and row
 *  render; sorting stays external — the tested sortBy/nextSort comparators in
 *  shmFilters get null-sinking right in a way TanStack's desc-negation would
 *  not — so the table runs with the rows already ordered (manual sort). */
function WatchTable({ rows, totalWatches, sort, onToggleSort, updatingSkin, onArm, onCommitCap, modelLabel }: {
  rows: ShmWatch[];
  totalWatches: number;
  sort: { key: string; dir: SortDir };
  onToggleSort: (key: string) => void;
  updatingSkin: number | null;
  onArm: (skinId: number, armed: boolean, watch: ShmWatch) => void;
  onCommitCap: (skinId: number, raw: string, current: number | null) => void;
  modelLabel: (id: number | null) => string;
}) {
  const columns: ColumnDef<ShmWatch>[] = [
    {
      id: "label",
      header: WATCH_SORTS.label.label,
      cell: ({ row }) => {
        const w = row.original;
        return (
          <div className="shm-watch-name">
            <Thumb skinId={w.skin_id} alt="" />
            <div>
              <strong title={w.label}>{watchLivery(w)}</strong>
              {/* Ownership rides the meta line rather than a line of its own: a
                  quarter of the list is owned, and a taller row for each makes
                  the table ragged. */}
              <div className="shm-watch-meta">
                <small>{watchModel(w) || modelLabel(w.model_id)} · skin {w.skin_id}</small>
                {w.is_owned && (
                  <span className="shm-owned" title={`${w.owned_count} in the hangar`}>
                    Owned ×{w.owned_count}
                  </span>
                )}
              </div>
            </div>
          </div>
        );
      },
    },
    {
      id: "source",
      header: WATCH_SORTS.source.label,
      cell: ({ row }) => {
        const w = row.original;
        return (
          <>
            <SourceChip source={w.source} />
            {w.origin && (
              <small className="shm-origin" title={w.origin_detail ?? w.origin}>{w.origin}</small>
            )}
          </>
        );
      },
    },
    {
      id: "state",
      header: WATCH_SORTS.state.label,
      cell: ({ row }) => {
        const w = row.original;
        const state = watchState(w);
        return (
          <>
            <label className="livery-toggle shm-arm-toggle" title={w.is_owned
              ? "You already own this livery — arming hunts for another copy"
              : STATE_LABELS.armed.hint}>
              <Checkbox
                checked={Boolean(w.armed)}
                disabled={updatingSkin === w.skin_id}
                onCheckedChange={(checked) => onArm(w.skin_id, checked === true, w)}
              />
              {w.armed ? STATE_LABELS.armed.label : STATE_LABELS.observing.label}
            </label>
            {!isLiveWatch(w) && (
              <small className={`shm-state ${STATE_LABELS[state].cls}`} title={STATE_LABELS[state].hint}>
                {STATE_LABELS[state].label}
              </small>
            )}
          </>
        );
      },
    },
    {
      id: "cap",
      header: WATCH_SORTS.cap.label,
      cell: ({ row }) => {
        const w = row.original;
        return (
          <Input
            className="shm-cap-input"
            key={`${w.skin_id}:${w.max_price ?? ""}`}
            defaultValue={w.max_price === null ? "" : shortMoney(w.max_price)}
            placeholder="No cap"
            title="Highest buy-now price to accept. Blank means no cap. Accepts 2b, 850m, or a plain number."
            disabled={updatingSkin === w.skin_id}
            onBlur={(event) => onCommitCap(w.skin_id, event.target.value, w.max_price)}
            onKeyDown={(event) => { if (event.key === "Enter") event.currentTarget.blur(); }}
          />
        );
      },
    },
    {
      id: "cheapest",
      header: WATCH_SORTS.cheapest.label,
      cell: ({ row }) => shortMoney(row.original.cheapest_seen),
      meta: { className: (w) => `shm-num ${capReach(w).cls}`, title: (w) => capReach(w).title },
    },
    {
      id: "last_price",
      header: WATCH_SORTS.last_price.label,
      cell: ({ row }) => shortMoney(row.original.last_price_seen),
      meta: { className: () => "shm-num" },
    },
    {
      id: "sightings",
      header: WATCH_SORTS.sightings.label,
      cell: ({ row }) => row.original.sightings || EMPTY,
      meta: { className: (w) => `shm-num${w.sightings ? "" : " is-empty"}` },
    },
    {
      id: "last_seen",
      header: WATCH_SORTS.last_seen.label,
      cell: ({ row }) => relativeTime(row.original.last_seen),
      meta: { className: (w) => (w.last_seen ? undefined : "is-empty") },
    },
  ];

  const table = useReactTable({ data: rows, columns, getCoreRowModel: getCoreRowModel() });

  return (
    <div className="shm-table-wrap">
      <Table className="shm-table">
        <TableHeader>
          {table.getHeaderGroups().map((group) => (
            <TableRow key={group.id}>
              {group.headers.map((header) => {
                const active = sort.key === header.column.id;
                const Arrow = !active ? ChevronsUpDown : sort.dir === 1 ? ArrowUp : ArrowDown;
                return (
                  <TableHead key={header.id} className={active ? "is-sorted" : undefined} aria-sort={!active ? "none" : sort.dir === 1 ? "ascending" : "descending"}>
                    <Button type="button" className="shm-th-sort" onClick={() => onToggleSort(header.column.id)}>
                      <span>{flexRender(header.column.columnDef.header, header.getContext())}</span>
                      <Arrow size={12} />
                    </Button>
                  </TableHead>
                );
              })}
            </TableRow>
          ))}
        </TableHeader>
        <TableBody>
          {table.getRowModel().rows.map((row) => {
            const w = row.original;
            return (
              <TableRow key={w.skin_id} className={isLiveWatch(w) ? undefined : "is-dormant"}>
                {row.getVisibleCells().map((cell) => {
                  const meta = cell.column.columnDef.meta;
                  return (
                    <TableCell key={cell.id} className={meta?.className?.(w)} title={meta?.title?.(w)}>
                      {flexRender(cell.column.columnDef.cell, cell.getContext())}
                    </TableCell>
                  );
                })}
              </TableRow>
            );
          })}
          {rows.length === 0 && (
            <TableRow><TableCell colSpan={WATCH_COLUMNS.length}>
              <EmptyState
                title={totalWatches ? "No watched livery matches" : "No liveries are being watched"}
                hint={totalWatches
                  ? "Adjust or clear the active filters."
                  : "Add one with shm_watch_add before the watcher has anything to hunt."}
              />
            </TableCell></TableRow>
          )}
        </TableBody>
      </Table>
    </div>
  );
}

interface ShmMonitorProps {
  refreshToken: number;
}

export function ShmMonitor({ refreshToken }: ShmMonitorProps) {
  const [snapshot, setSnapshot] = useState<ShmMonitorSnapshot | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [updatingSkin, setUpdatingSkin] = useState<number | null>(null);
  const [filters, setFilters] = useState<WatchFilters>(NO_WATCH_FILTERS);
  // "priority" is not a column: it means "leave the server's owned-last order alone".
  const [sort, setSort] = useState<{ key: string; dir: SortDir }>({ key: "priority", dir: 1 });
  // Activity feeds open on their newest rows; the alphabetical sorts are there
  // for scanning a long feed, not for reading one.
  const [sightingFilters, setSightingFilters] = useState<FeedFilters>(NO_FEED_FILTERS);
  const [sightingSort, setSightingSort] = useState("last_seen:-1");
  const [checkFilters, setCheckFilters] = useState<FeedFilters>(NO_FEED_FILTERS);
  const [checkSort, setCheckSort] = useState("last_checked:-1");
  const [decisionFilters, setDecisionFilters] = useState<FeedFilters>(NO_FEED_FILTERS);
  const [decisionSort, setDecisionSort] = useState("bought_at:-1");

  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      try {
        const data = await fetchShmMonitor();
        if (!cancelled) {
          setSnapshot(data);
          setError(null);
        }
      } catch (reason) {
        if (!cancelled) {
          setError(reason instanceof Error ? reason.message : "Failed to load watcher activity");
        }
      }
    };
    load();
    const timer = window.setInterval(load, 15_000);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [refreshToken]);

  if (error && !snapshot) return <ErrorState title="SHM monitor unavailable" message={error} />;
  if (!snapshot) return <LoadingState />;

  const { status, summary, watches, model_checks: checks, sightings, decisions } = snapshot;
  const models = modelNames(watches);
  const modelLabel = (id: number | null) => (id === null ? "Unknown model" : models.get(id) ?? `Model ${id}`);

  const patchWatch = async (
    skinId: number,
    patch: { armed?: boolean; max_price?: number | null },
    message: string,
  ) => {
    setUpdatingSkin(skinId);
    try {
      await updateShmWatch(skinId, patch);
      setSnapshot((current) => current ? {
        ...current,
        watches: current.watches.map((watch) => watch.skin_id !== skinId ? watch : {
          ...watch,
          // set_watch_armed re-opens a dormant row for one more copy; mirror
          // that here so the row does not keep reading "Acquired" until the
          // next poll.
          ...(patch.armed === undefined ? {} : patch.armed
            ? { armed: 1, active: 1, want: Math.max(watch.want, watch.bought + 1) }
            : { armed: 0 }),
          ...(patch.max_price === undefined ? {} : { max_price: patch.max_price }),
        }),
      } : current);
      setError(null);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : message);
    } finally {
      setUpdatingSkin(null);
    }
  };

  const toggleArm = (skinId: number, armed: boolean, watch: ShmWatch) => {
    // Arming a livery already in the hangar spends real money on a duplicate,
    // so it takes a deliberate yes rather than a stray click. It is a real
    // workflow — the scarce liveries are trade stock — just not an accident.
    if (armed && watch.is_owned && !window.confirm(
      `You already own ${watch.label}. Arm it anyway and buy another copy?`)) return;
    void patchWatch(skinId, { armed }, "Failed to update watch arming");
  };

  const commitCap = (skinId: number, raw: string, current: number | null) => {
    const parsed = parseMoney(raw);
    if (parsed === undefined || parsed === current) return;
    void patchWatch(skinId, { max_price: parsed }, "Failed to update price cap");
  };

  const watchBySkin = new Map(watches.map((watch) => [watch.skin_id, watch]));
  const setFilter = (patch: Partial<WatchFilters>) => setFilters((current) => ({ ...current, ...patch }));
  const visibleWatches = filterWatches(watches, filters).sort(sortBy(WATCH_SORTS, sort.key, sort.dir));
  const activeFilters = countWatchFilters(filters);
  // Counted off the rows, not read from `summary`: these three are exactly what
  // isLiveWatch/watchState already decide per row, and arming a dormant watch
  // re-opens it — a second copy of the number would lag a poll behind.
  const liveWatches = watches.filter(isLiveWatch).length;
  const armedWatches = watches.filter((watch) => watchState(watch) === "armed").length;
  const packWatches = watches.filter(
    (watch) => isLiveWatch(watch) && sourceBucket(watch.source) === "shop-pack:auto").length;

  const visibleSightings = filterSightings(sightings, sightingFilters, modelLabel)
    .sort(sortByValue(SIGHTING_SORTS, sightingSort));
  const CHECK_SORTS = checkSorts(modelLabel);
  const visibleChecks = filterChecks(checks, checkFilters, modelLabel)
    .sort(sortByValue(CHECK_SORTS, checkSort));
  const groupedDecisions = groupDecisions(decisions);
  const visibleDecisions = filterDecisions(groupedDecisions, decisionFilters, modelLabel)
    .sort(sortByValue(DECISION_SORTS, decisionSort));
  const truncatedChecks = checks.filter((check) => check.truncated).length;

  // Clicking a column heading sorts by it; clicking it again reverses, and a
  // third click hands the order back to the server's buying priority.
  const toggleSort = (key: string) => setSort((current) => nextSort(WATCH_SORTS, current, key, "priority"));

  return (
    <div className="shm-workspace">
      {error && <div className="inline-notice"><span>{error}</span></div>}

      <section className={`portfolio-strip shm-strip${status.observing ? " is-live" : ""}`} aria-label="SHM watcher summary">
        <div className="portfolio-lead">
          <span className="summary-label"><Radar size={13} /> Watcher</span>
          <strong>{status.observing ? "Observing" : "Idle"}</strong>
          <small>
            Last market read {relativeTime(status.last_activity)} · {integer.format(summary.watched_models)} models covered
            {truncatedChecks > 0 && ` · ${truncatedChecks} truncated`}
          </small>
        </div>
        <div className="summary-divider" />
        <div className="summary-stat">
          <span><Eye size={12} /> Standing orders</span>
          <strong>{integer.format(liveWatches)}</strong>
          <small>{integer.format(watches.length - liveWatches)} dormant</small>
        </div>
        <div className="summary-stat tone-amber">
          <span><ShieldCheck size={12} /> Armed</span>
          <strong>{integer.format(armedWatches)}</strong>
          <small>will spend on sight</small>
        </div>
        <div className="summary-stat">
          <span><ShoppingBag size={12} /> Paid packs</span>
          <strong>{integer.format(packWatches)}</strong>
          <small>shop-only liveries</small>
        </div>
        <div className="summary-stat">
          <span><Activity size={12} /> Matched listings</span>
          <strong>{integer.format(summary.matched_sightings)}</strong>
          <small>seen all time</small>
        </div>
        <div className="summary-stat">
          <span><Gavel size={12} /> Bought today</span>
          <strong>{integer.format(summary.real_buys_today)}</strong>
          <small>{integer.format(summary.dry_runs_today)} dry runs</small>
        </div>
      </section>

      <section className="flat-section shm-section">
        <SectionHeader kicker="Standing orders" title="Watched liveries" count={<>{integer.format(liveWatches)} live · {integer.format(watches.length)} total</>} />
        <FilterBar className="shm-controls" active={activeFilters} onClear={() => setFilters(NO_WATCH_FILTERS)}
          status={<small>Showing {integer.format(visibleWatches.length)} of {integer.format(watches.length)}</small>}>
          <SearchInput value={filters.query} onChange={(query) => setFilter({ query })} placeholder="Search livery, model, or id" />
          <MenuSelect label="Source" value={filters.source} onChange={(source) => setFilter({ source })} groups={sourceGroups(watches)} />
          <MenuSelect label="Standing order" value={filters.state} onChange={(state) => setFilter({ state })} options={STATE_OPTIONS} icon={ShieldCheck} />
          <MenuSelect label="Ownership" value={filters.ownership} onChange={(ownership) => setFilter({ ownership })} options={OWNERSHIP_OPTIONS} />
        </FilterBar>
        <WatchTable
          rows={visibleWatches}
          totalWatches={watches.length}
          sort={sort}
          onToggleSort={toggleSort}
          updatingSkin={updatingSkin}
          onArm={toggleArm}
          onCommitCap={commitCap}
          modelLabel={modelLabel}
        />
      </section>

      <div className="shm-columns">
        <section className="flat-section shm-section">
          <SectionHeader kicker="Matches" title="Recent watched listings" count={<>Latest {integer.format(sightings.length)} recorded</>} />
          <FeedControls
            filters={sightingFilters}
            onChange={setSightingFilters}
            options={SIGHTING_FILTER_OPTIONS}
            placeholder="Search livery, model, or auction id"
            sort={sightingSort}
            onSort={setSightingSort}
            sortSpecs={SIGHTING_SORTS}
            shown={visibleSightings.length}
            total={sightings.length}
          />
          <div className="shm-feed is-sightings">
            {visibleSightings.map((item) => {
              const ending = item.time_left_s !== null && item.time_left_s < 3600;
              return (
                <article key={item.auction_id}>
                  <Thumb skinId={item.skin_id} alt="" />
                  <div>
                    <strong title={item.skin_name}>{item.skin_name}</strong>
                    <small>{modelLabel(item.model_id)} · auction {item.auction_id}</small>
                  </div>
                  <div className="shm-metric">
                    <strong>{shortMoney(item.bin_price)}</strong>
                    <small>buy now</small>
                  </div>
                  <div className="shm-metric">
                    <strong>{shortMoney(item.current_price)}</strong>
                    <small>{item.bids ? `${item.bids} bid${item.bids === 1 ? "" : "s"}` : "no bids"}</small>
                  </div>
                  <div className={`shm-metric${ending ? " is-ending" : ""}`}>
                    <strong>{duration(item.time_left_s)}</strong>
                    <small>left</small>
                  </div>
                  <time title={`Last seen ${item.last_seen} UTC`}>{relativeTime(item.last_seen)}</time>
                </article>
              );
            })}
            {visibleSightings.length === 0 && (
              <EmptyState
                title={sightings.length ? "No listing matches" : "No watched livery has appeared yet"}
                hint={sightings.length
                  ? "Adjust or clear the active filters."
                  : "A match is recorded the first time one of these liveries is listed."}
              />
            )}
          </div>
        </section>

        <section className="flat-section shm-section is-narrow">
          <SectionHeader kicker="Coverage" title="Model sweeps" count={<>{integer.format(truncatedChecks)} truncated</>} />
          <FeedControls
            filters={checkFilters}
            onChange={setCheckFilters}
            options={CHECK_FILTER_OPTIONS}
            placeholder="Search model"
            sort={checkSort}
            onSort={setCheckSort}
            sortSpecs={CHECK_SORTS}
            shown={visibleChecks.length}
            total={checks.length}
          />
          <div className="shm-feed is-checks">
            {visibleChecks.map((check) => (
              <article key={check.model_id}>
                <div>
                  <strong>{modelLabel(check.model_id)}</strong>
                  <small>model {check.model_id} · {integer.format(check.checks)} sweeps</small>
                </div>
                <span
                  className={`shm-chip${check.truncated ? " is-warning" : ""}`}
                  title={check.truncated
                    ? "The 100-row window filled up, so this sweep did not see every listing of the model"
                    : "Every live listing of this model was returned"}
                >
                  {check.truncated ? "Truncated" : "Complete"}
                </span>
                <time>{relativeTime(check.last_checked)}</time>
              </article>
            ))}
            {visibleChecks.length === 0 && (
              <EmptyState
                title={checks.length ? "No model sweep matches" : "No model sweeps recorded yet"}
                hint={checks.length
                  ? "Adjust or clear the active filters."
                  : "The watcher records one sweep per watched model, per pass."}
              />
            )}
          </div>
        </section>
      </div>

      <section className="flat-section shm-section">
        <SectionHeader kicker="Audit trail" title="Purchase decisions" count={<>{integer.format(summary.dry_runs_today)} dry runs today</>} />
        <FeedControls
          filters={decisionFilters}
          onChange={setDecisionFilters}
          options={DECISION_FILTER_OPTIONS}
          placeholder="Search livery, model, auction id, or note"
          sort={decisionSort}
          onSort={setDecisionSort}
          sortSpecs={DECISION_SORTS}
          shown={visibleDecisions.length}
          total={groupedDecisions.length}
        />
        <div className="shm-feed is-decisions">
          {visibleDecisions.map((decision) => {
            const guard = decisionGuard(decision.bin_price, watchBySkin.get(decision.skin_id));
            return (
            <article key={decision.auction_id}>
              <Thumb skinId={decision.skin_id} alt="" />
              <div>
                <strong title={decision.skin_name}>{decision.skin_name}</strong>
                <small>{modelLabel(decision.model_id)} · auction {decision.auction_id}{decision.note ? ` · ${decision.note}` : ""}</small>
              </div>
              <div className="shm-metric">
                <strong>{shortMoney(decision.bin_price)}</strong>
                {/* The accepted cost is exactly the submitted BIN, so the estimate
                    is only worth a line when something moved it. */}
                <small>{decision.est_cost === decision.bin_price ? "buy now" : `est. ${shortMoney(decision.est_cost)}`}</small>
              </div>
              {/* The guard that actually decided this row. */}
              <div className={`shm-metric${guard.blocked ? " is-over" : ""}`}>
                <strong>{guard.value}</strong>
                <small>{guard.label}</small>
              </div>
              <span className={`shm-chip${decision.dry_run ? "" : " is-live"}`}>{decision.dry_run ? "Dry run" : decision.confirmed ? "Confirmed" : "Live"}</span>
              <span className="shm-repeats" title={`Weighed ${decision.repeats} times in the latest ${decisions.length} decisions`}>
                ×{decision.repeats}
              </span>
              <time><Clock3 size={11} /> {relativeTime(decision.bought_at)}</time>
            </article>
            );
          })}
          {visibleDecisions.length === 0 && (
            <EmptyState
              title={decisions.length ? "No decision matches" : "No purchase decisions recorded"}
              hint={decisions.length
                ? "Adjust or clear the active filters."
                : "Every weighed listing lands here, dry runs included."}
            />
          )}
        </div>
      </section>
    </div>
  );
}
