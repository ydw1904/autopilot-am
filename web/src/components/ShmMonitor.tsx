import { useEffect, useState } from "react";
import {
  Activity,
  ArrowDown,
  ArrowUp,
  ChevronsUpDown,
  Clock3,
  Eye,
  Filter,
  Gavel,
  Gift,
  Hand,
  Package,
  Plane,
  Radar,
  Search,
  ShieldCheck,
  ShoppingBag,
  Ticket,
  Trophy,
  X,
} from "lucide-react";
import { fetchShmMonitor, updateShmWatch } from "../api";
import { ShmMonitorSnapshot, ShmWatch } from "../types";
import { MenuOption, MenuSelect } from "./MenuSelect";
import {
  CHECK_SORTS,
  DECISION_SORTS,
  NO_WATCH_FILTERS,
  SIGHTING_SORTS,
  STATE_LABELS,
  SortDir,
  SortSpec,
  WATCH_COLUMNS,
  WATCH_SORTS,
  WatchFilters,
  countWatchFilters,
  filterChecks,
  filterDecisions,
  filterWatches,
  groupDecisions,
  isLiveWatch,
  modelNames,
  nextSort,
  sortBy,
  sourceBucket,
  sourceStyle,
  watchLivery,
  watchModel,
  watchState,
} from "./shmFilters";

const integer = new Intl.NumberFormat();

const SOURCE_ICONS: Record<string, typeof Trophy> = {
  "is-shop-pack": ShoppingBag,
  "is-shop-tc": Ticket,
  "is-shop-gift": Gift,
  "is-challenge": Trophy,
  "is-booster": Package,
  "is-manual": Hand,
};

const SOURCE_OPTIONS: MenuOption[] = [
  { value: "all", label: "All sources" },
  { value: "shop-pack:auto", label: "Paid pack" },
  { value: "shop-ticket:auto", label: "Ticket aircraft", hint: "costs travel cards" },
  { value: "shop-gold:auto", label: "AM Gold Crew", hint: "monthly subscription reward" },
  { value: "challenge:auto", label: "Challenge" },
  { value: "booster", label: "Booster" },
  { value: "manual", label: "Manual" },
];
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

/** The feed sections pick their sort column from the same specs the table sorts by. */
function sortOptions<T>(specs: Record<string, SortSpec<T>>): MenuOption[] {
  return Object.entries(specs).map(([value, spec]) => ({ value, label: spec.label }));
}

/** Ascending/descending toggle sitting next to a feed's sort dropdown. */
function DirButton({ dir, onFlip }: { dir: SortDir; onFlip: () => void }) {
  return (
    <button
      type="button"
      className="sort-dir-button"
      onClick={onFlip}
      title={dir === 1 ? "Ascending — click for descending" : "Descending — click for ascending"}
      aria-label={dir === 1 ? "Sorted ascending" : "Sorted descending"}
    >
      {dir === 1 ? <ArrowUp size={14} /> : <ArrowDown size={14} />}
    </button>
  );
}

const EMPTY = "—";

function money(value: number | null | undefined): string {
  if (value === null || value === undefined) return EMPTY;
  if (value >= 1_000_000_000) return `$${(value / 1_000_000_000).toFixed(2)}B`;
  if (value >= 1_000_000) return `$${(value / 1_000_000).toFixed(0)}M`;
  return `$${integer.format(value)}`;
}

// Accepts what money() prints back ("$2.00B", "$1,250"), plus the shorthand the
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

function utcDate(value: string | null | undefined): Date | null {
  if (!value) return null;
  const parsed = new Date(value.includes("T") ? value : `${value.replace(" ", "T")}Z`);
  return Number.isNaN(parsed.getTime()) ? null : parsed;
}

function relativeTime(value: string | null | undefined): string {
  const date = utcDate(value);
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
    ? { cls: "is-under-cap", title: `Within the ${money(watch.max_price)} cap` }
    : { cls: "is-over-cap", title: `Above the ${money(watch.max_price)} cap — this watch cannot fire at that price` };
}

/** Why a weighed listing was left alone. The watcher takes a listing only at
 *  its own buy-now price, and only from a live, armed watch under its cap — so
 *  the first of those that fails is the whole story of the row. */
function decisionGuard(binPrice: number, watch: ShmWatch | undefined): { value: string; label: string; blocked: boolean } {
  if (!watch) return { value: EMPTY, label: "no longer watched", blocked: false };
  if (watch.max_price !== null && binPrice > watch.max_price) {
    return { value: money(watch.max_price), label: "over cap", blocked: true };
  }
  if (!isLiveWatch(watch)) return { value: STATE_LABELS[watchState(watch)].label, label: "dormant", blocked: true };
  if (!watch.armed) return { value: "Observe", label: "not armed", blocked: true };
  return { value: money(watch.max_price), label: "price cap", blocked: false };
}

interface ShmMonitorProps {
  refreshToken: number;
}

export function ShmMonitor({ refreshToken }: ShmMonitorProps) {
  const [snapshot, setSnapshot] = useState<ShmMonitorSnapshot | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [updatingSkin, setUpdatingSkin] = useState<number | null>(null);
  const [filters, setFilters] = useState<WatchFilters>(NO_WATCH_FILTERS);
  // "priority" is not a column: it means "leave the server's owned-last order alone".
  const [sort, setSort] = useState<{ key: string; dir: SortDir }>({ key: "priority", dir: 1 });
  const [sightingSort, setSightingSort] = useState<{ key: string; dir: SortDir }>({ key: "last_seen", dir: -1 });
  const [checkFilter, setCheckFilter] = useState("all");
  const [checkSort, setCheckSort] = useState<{ key: string; dir: SortDir }>({ key: "last_checked", dir: -1 });
  const [decisionFilter, setDecisionFilter] = useState("all");
  const [decisionSort, setDecisionSort] = useState<{ key: string; dir: SortDir }>({ key: "bought_at", dir: -1 });

  useEffect(() => {
    let cancelled = false;
    const load = async (quiet = false) => {
      if (!quiet) setLoading(true);
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
      } finally {
        if (!cancelled) setLoading(false);
      }
    };
    load();
    const timer = window.setInterval(() => load(true), 15_000);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [refreshToken]);

  if (loading && !snapshot) {
    return <div className="fleet-loading">Loading SHM watcher activity…</div>;
  }
  if (error && !snapshot) {
    return <div className="table-error">{error}</div>;
  }
  if (!snapshot) return null;

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

  const visibleSightings = [...sightings].sort(sortBy(SIGHTING_SORTS, sightingSort.key, sightingSort.dir));
  const visibleChecks = filterChecks(checkFilter, checks).slice().sort(sortBy(CHECK_SORTS, checkSort.key, checkSort.dir));
  const groupedDecisions = groupDecisions(decisions);
  const visibleDecisions = filterDecisions(decisionFilter, groupedDecisions)
    .sort(sortBy(DECISION_SORTS, decisionSort.key, decisionSort.dir));
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
        <div className="section-title-row">
          <div>
            <p className="section-kicker">Standing orders</p>
            <h2>Watched liveries</h2>
          </div>
          <span className="section-count">{integer.format(liveWatches)} live · {integer.format(watches.length)} total</span>
        </div>
        <section className="fleet-controls shm-controls">
          <div className="fleet-toolbar">
            <label className="search-control">
              <Search size={17} />
              <input value={filters.query} onChange={(event) => setFilter({ query: event.target.value })} placeholder="Search livery or skin id" />
              {filters.query && <button onClick={() => setFilter({ query: "" })} aria-label="Clear search"><X size={15} /></button>}
            </label>
            <MenuSelect label="Source" value={filters.source} onChange={(source) => setFilter({ source })} options={SOURCE_OPTIONS} />
            <MenuSelect label="Standing order" value={filters.state} onChange={(state) => setFilter({ state })} options={STATE_OPTIONS} icon={ShieldCheck} />
            <MenuSelect label="Ownership" value={filters.ownership} onChange={(ownership) => setFilter({ ownership })} options={OWNERSHIP_OPTIONS} />
            <input
              className="model-input"
              value={filters.model}
              onChange={(event) => setFilter({ model: event.target.value })}
              placeholder="Model name or id"
              aria-label="Filter by model"
            />
          </div>
          <div className="active-filter-row">
            <span><Filter size={14} /> {activeFilters ? `${activeFilters} active filter${activeFilters === 1 ? "" : "s"}` : "No filters applied"}</span>
            {activeFilters > 0 && <button onClick={() => setFilters(NO_WATCH_FILTERS)}>Clear all</button>}
            <small>Showing {integer.format(visibleWatches.length)} of {integer.format(watches.length)}</small>
          </div>
        </section>
        <div className="shm-table-wrap">
          <table className="shm-table">
            <thead>
              <tr>
                {WATCH_COLUMNS.map((key) => {
                  const active = sort.key === key;
                  const Arrow = !active ? ChevronsUpDown : sort.dir === 1 ? ArrowUp : ArrowDown;
                  return (
                    <th key={key} className={active ? "is-sorted" : undefined} aria-sort={!active ? "none" : sort.dir === 1 ? "ascending" : "descending"}>
                      <button type="button" className="shm-th-sort" onClick={() => toggleSort(key)}>
                        <span>{WATCH_SORTS[key].label}</span>
                        <Arrow size={12} />
                      </button>
                    </th>
                  );
                })}
              </tr>
            </thead>
            <tbody>
              {visibleWatches.map((watch) => {
                const state = watchState(watch);
                const live = isLiveWatch(watch);
                const reach = capReach(watch);
                return (
                  <tr key={watch.skin_id} className={live ? undefined : "is-dormant"}>
                    <td>
                      <div className="shm-watch-name">
                        <Thumb skinId={watch.skin_id} alt="" />
                        <div>
                          <strong title={watch.label}>{watchLivery(watch)}</strong>
                          {/* Ownership rides the meta line rather than a line of
                              its own: a quarter of the list is owned, and a
                              taller row for each of them makes the table ragged. */}
                          <div className="shm-watch-meta">
                            <small>{watchModel(watch) || modelLabel(watch.model_id)} · skin {watch.skin_id}</small>
                            {watch.is_owned && (
                              <span className="shm-owned" title={`${watch.owned_count} in the hangar`}>
                                Owned ×{watch.owned_count}
                              </span>
                            )}
                          </div>
                        </div>
                      </div>
                    </td>
                    <td>
                      <SourceChip source={watch.source} />
                      {watch.origin && (
                        <small className="shm-origin" title={watch.origin_detail ?? watch.origin}>
                          {watch.origin}
                        </small>
                      )}
                    </td>
                    <td>
                      <label className="livery-toggle shm-arm-toggle" title={watch.is_owned
                        ? "You already own this livery — arming hunts for another copy"
                        : STATE_LABELS.armed.hint}>
                        <input
                          type="checkbox"
                          checked={Boolean(watch.armed)}
                          disabled={updatingSkin === watch.skin_id}
                          onChange={(event) => toggleArm(watch.skin_id, event.target.checked, watch)}
                        />
                        {watch.armed ? STATE_LABELS.armed.label : STATE_LABELS.observing.label}
                      </label>
                      {!live && (
                        <small className={`shm-state ${STATE_LABELS[state].cls}`} title={STATE_LABELS[state].hint}>
                          {STATE_LABELS[state].label}
                        </small>
                      )}
                    </td>
                    <td>
                      <input
                        className="shm-cap-input"
                        key={`${watch.skin_id}:${watch.max_price ?? ""}`}
                        defaultValue={watch.max_price === null ? "" : money(watch.max_price)}
                        placeholder="No cap"
                        title="Highest buy-now price to accept. Blank means no cap. Accepts 2b, 850m, or a plain number."
                        disabled={updatingSkin === watch.skin_id}
                        onBlur={(event) => commitCap(watch.skin_id, event.target.value, watch.max_price)}
                        onKeyDown={(event) => { if (event.key === "Enter") event.currentTarget.blur(); }}
                      />
                    </td>
                    <td className={`shm-num ${reach.cls}`} title={reach.title}>{money(watch.cheapest_seen)}</td>
                    <td className="shm-num">{money(watch.last_price_seen)}</td>
                    <td className={`shm-num${watch.sightings ? "" : " is-empty"}`}>{watch.sightings || EMPTY}</td>
                    <td className={watch.last_seen ? undefined : "is-empty"}>{relativeTime(watch.last_seen)}</td>
                  </tr>
                );
              })}
              {visibleWatches.length === 0 && <tr><td colSpan={WATCH_COLUMNS.length} className="shm-empty">{watches.length ? "No watched livery matches those filters." : "No liveries are being watched."}</td></tr>}
            </tbody>
          </table>
        </div>
      </section>

      <div className="shm-columns">
        <section className="flat-section shm-section">
          <div className="section-title-row">
            <div><p className="section-kicker">Matches</p><h2>Recent watched listings</h2></div>
            <div className="section-header-controls">
              <MenuSelect label="Sort by" value={sightingSort.key} onChange={(key) => setSightingSort({ key, dir: SIGHTING_SORTS[key].dir })} options={sortOptions(SIGHTING_SORTS)} align="right" />
              <DirButton dir={sightingSort.dir} onFlip={() => setSightingSort((s) => ({ ...s, dir: (s.dir === 1 ? -1 : 1) as SortDir }))} />
              <span className="section-count">Latest {sightings.length}</span>
            </div>
          </div>
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
                    <strong>{money(item.bin_price)}</strong>
                    <small>buy now</small>
                  </div>
                  <div className="shm-metric">
                    <strong>{money(item.current_price)}</strong>
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
            {sightings.length === 0 && <p className="shm-empty">No watched livery has appeared yet.</p>}
          </div>
        </section>

        <section className="flat-section shm-section is-narrow">
          <div className="section-title-row">
            <div><p className="section-kicker">Coverage</p><h2>Model sweeps</h2></div>
            <div className="section-header-controls">
              <MenuSelect label="Filter" value={checkFilter} onChange={setCheckFilter} options={CHECK_FILTER_OPTIONS} />
              <MenuSelect label="Sort by" value={checkSort.key} onChange={(key) => setCheckSort({ key, dir: CHECK_SORTS[key].dir })} options={sortOptions(CHECK_SORTS)} align="right" />
              <DirButton dir={checkSort.dir} onFlip={() => setCheckSort((s) => ({ ...s, dir: (s.dir === 1 ? -1 : 1) as SortDir }))} />
            </div>
          </div>
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
            {visibleChecks.length === 0 && <p className="shm-empty">{checks.length ? "No model sweeps match that filter." : "No model sweeps recorded yet."}</p>}
          </div>
        </section>
      </div>

      <section className="flat-section shm-section">
        <div className="section-title-row">
          <div>
            <p className="section-kicker">Audit trail</p>
            <h2>Purchase decisions</h2>
          </div>
          <div className="section-header-controls">
            <MenuSelect label="Filter" value={decisionFilter} onChange={setDecisionFilter} options={DECISION_FILTER_OPTIONS} />
            <MenuSelect label="Sort by" value={decisionSort.key} onChange={(key) => setDecisionSort({ key, dir: DECISION_SORTS[key].dir })} options={sortOptions(DECISION_SORTS)} align="right" />
            <DirButton dir={decisionSort.dir} onFlip={() => setDecisionSort((s) => ({ ...s, dir: (s.dir === 1 ? -1 : 1) as SortDir }))} />
            <span className="section-count">{integer.format(summary.dry_runs_today)} dry runs today</span>
          </div>
        </div>
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
                <strong>{money(decision.bin_price)}</strong>
                {/* The accepted cost is exactly the submitted BIN, so the estimate
                    is only worth a line when something moved it. */}
                <small>{decision.est_cost === decision.bin_price ? "buy now" : `est. ${money(decision.est_cost)}`}</small>
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
          {visibleDecisions.length === 0 && <p className="shm-empty">{decisions.length ? "No purchase decisions match that filter." : "No purchase decisions recorded."}</p>}
        </div>
      </section>
    </div>
  );
}
