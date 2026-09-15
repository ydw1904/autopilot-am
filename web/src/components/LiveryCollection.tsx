import { useEffect, useMemo, useState } from "react";
import {
  ArrowDownAZ,
  ArrowDownWideNarrow,
  ArrowUpNarrowWide,
  Award,
  CalendarClock,
  CheckCircle2,
  Clock,
  Coins,
  Gift,
  Package,
  Palette,
  Plane,
  RefreshCcw,
  ShoppingBag,
  Sparkles,
  Store,
  Ticket,
  Trophy,
  Tv,
  Wand2,
  X,
  XCircle,
} from "lucide-react";
import { fetchLiveries, triggerSyncLiveries } from "../api";
import { parseGameDate } from "../format";
import { LiveryItem, LiveryTag, LiveryTagKind } from "../types";
import { challengeTag, displayLiveryName, splitLiveryName } from "../liveryName";
import { AircraftListModal } from "./AircraftListModal";
import { FilterBar, SearchInput, useDebouncedQuery } from "./FilterBar";
import { MenuOption, MenuSelect } from "./MenuSelect";
import { EmptyState, ErrorState } from "./PageStates";
import { SegmentedControl } from "./SegmentedControl";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";

interface LiveryCollectionProps {
  refreshToken: number;
  onViewInFleet: (liveryName: string) => void;
  initialPreset?: string;
  onPresetCleared: () => void;
}

type StatusFilter = "all" | "owned" | "unowned";
type SortMode = "livery_name" | "name" | "owned_desc" | "owned_asc" | "added_desc" | "added_asc";
// One chip fits on a single line; anything beyond it collapses into the
// "+N more" modal so every card in the grid stays exactly the same height.
const CHIP_LIMIT = 1;

// Every way a livery can be obtained renders as the same chip, so a livery
// that comes from two places reads as two tags rather than two layouts. The
// icon and colour carry which source it is; `cls` maps to the palette in
// index.css.
const TAG_STYLES: Record<LiveryTagKind, { cls: string; Icon: typeof Trophy }> = {
  challenge: { cls: "is-challenge", Icon: Trophy },
  booster: { cls: "is-booster", Icon: Package },
  shop_gift: { cls: "is-shop-gift", Icon: Gift },
  shop_ad: { cls: "is-shop-ad", Icon: Tv },
  shop_amc: { cls: "is-shop-amc", Icon: Coins },
  shop_tc: { cls: "is-shop-tc", Icon: Ticket },
  shop_pack: { cls: "is-shop-pack", Icon: ShoppingBag },
  dutyfree: { cls: "is-dutyfree", Icon: Store },
  market: { cls: "is-custom", Icon: Wand2 },
};

// "Livery name" groups liveries by the name half first, then orders the models
// within each group; "Full name" keeps the raw model-first string. Both use
// plain A-to-Z (numbers sort before letters), matching the fleet sort menu.
const SORT_OPTIONS: MenuOption[] = [
  { value: "livery_name", label: "Livery name", hint: "A to Z, then model", icon: ArrowDownAZ },
  { value: "name", label: "Full name", hint: "Model first, A to Z", icon: Plane },
  { value: "owned_desc", label: "Planes in fleet", hint: "Most first", icon: ArrowDownWideNarrow },
  { value: "owned_asc", label: "Planes in fleet", hint: "Fewest first", icon: ArrowUpNarrowWide },
  { value: "added_desc", label: "Date added", hint: "Newest scrape first", icon: CalendarClock },
  { value: "added_asc", label: "Date added", hint: "Oldest scrape first", icon: Clock },
];
const DEFAULT_SORT = SORT_OPTIONS[0].value as SortMode;

// A livery counts as "just scraped" for this many days after it first landed in
// the DB, which is long enough that a sync done a few days ago is still obvious.
const NEW_WINDOW_DAYS = 7;

// `first_seen` comes from SQLite's datetime('now'): "YYYY-MM-DD HH:MM:SS" in UTC
// with no zone marker, which browsers would otherwise read as local time.
function parseAdded(value: string | null | undefined): number {
  return parseGameDate(value)?.getTime() ?? NaN;
}

// Rows with no timestamp sort to the bottom of "newest first" and the top of
// "oldest first", rather than landing in the middle as NaN comparisons.
function addedAt(item: LiveryItem): number {
  const stamp = parseAdded(item.first_seen);
  return Number.isNaN(stamp) ? 0 : stamp;
}

function addedLabel(value: string | null | undefined): string | null {
  const stamp = parseAdded(value);
  if (Number.isNaN(stamp)) return null;
  return new Date(stamp).toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" });
}

function daysSinceAdded(value: string | null | undefined): number | null {
  const stamp = parseAdded(value);
  if (Number.isNaN(stamp)) return null;
  return Math.floor((Date.now() - stamp) / 86_400_000);
}

// User-created (market) liveries are hidden by default and the choice persists
// across sessions, so the collection stays limited to official skins unless
// the operator opts back in.
const SHOW_USER_CREATED_KEY = "livery:showUserCreated";
function loadShowUserCreated(): boolean {
  try {
    return window.localStorage.getItem(SHOW_USER_CREATED_KEY) === "true";
  } catch {
    return false;
  }
}

export function LiveryCollection({ refreshToken, onViewInFleet, initialPreset, onPresetCleared }: LiveryCollectionProps) {
  const [liveries, setLiveries] = useState<LiveryItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [status, setStatus] = useState<StatusFilter>("all");
  const [search, setSearch] = useState("");
  const debouncedSearch = useDebouncedQuery(search);
  const [model, setModel] = useState("");
  const debouncedModel = useDebouncedQuery(model);
  const [sort, setSort] = useState<SortMode>(DEFAULT_SORT);
  const [showUserCreated, setShowUserCreated] = useState<boolean>(loadShowUserCreated);
  const [modalItem, setModalItem] = useState<LiveryItem | null>(null);
  const [focusSkinId, setFocusSkinId] = useState<number | null>(null);
  const [syncing, setSyncing] = useState(false);
  const [syncNotice, setSyncNotice] = useState<string | null>(null);
  const [catalogVersion, setCatalogVersion] = useState(0);

  useEffect(() => {
    try {
      window.localStorage.setItem(SHOW_USER_CREATED_KEY, String(showUserCreated));
    } catch {
      /* ignore persistence failures (private mode, etc.) */
    }
  }, [showUserCreated]);

  // Arriving from a fleet "Liveries of the day" card: that card is one exact
  // skin, and a livery name is shared by every model that carries it (there is
  // a "Glueck Super 100" for several airframes), so the preset pins the skin id
  // and the collection shows that single card. `livery:<name>` stays supported
  // for hand-typed links and seeds the search box as before.
  useEffect(() => {
    if (initialPreset?.startsWith("skin:")) {
      const skinId = Number(initialPreset.slice("skin:".length));
      setFocusSkinId(Number.isFinite(skinId) ? skinId : null);
    } else if (initialPreset?.startsWith("livery:")) {
      setFocusSkinId(null);
      setSearch(initialPreset.slice("livery:".length));
    } else {
      setFocusSkinId(null);
    }
  }, [initialPreset]);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    fetchLiveries({
      status_filter: status,
      model_query: debouncedModel || undefined,
      search_query: debouncedSearch || undefined,
      // A pinned skin can be a player-designed market livery, which the toggle
      // hides by default; include those while a focus is active so the card the
      // fleet page pointed at cannot come back empty.
      include_user_created: showUserCreated || focusSkinId !== null,
    }).then((data) => {
      if (cancelled) return;
      const sorted = [...data].sort((a, b) => {
        if (sort === "owned_desc") return b.owned_count - a.owned_count || a.name.localeCompare(b.name);
        if (sort === "owned_asc") return a.owned_count - b.owned_count || a.name.localeCompare(b.name);
        if (sort === "added_desc") return addedAt(b) - addedAt(a) || a.name.localeCompare(b.name);
        if (sort === "added_asc") return addedAt(a) - addedAt(b) || a.name.localeCompare(b.name);
        if (sort === "livery_name") {
          const sa = splitLiveryName(a.name);
          const sb = splitLiveryName(b.name);
          return sa.livery.localeCompare(sb.livery) || sa.model.localeCompare(sb.model);
        }
        return a.name.localeCompare(b.name);
      });
      setLiveries(sorted);
      if (modalItem && !sorted.some((item) => item.skin_id === modalItem.skin_id)) setModalItem(null);
    }).catch((reason) => {
      if (!cancelled) setError(reason instanceof Error ? reason.message : "Failed to load liveries");
    }).finally(() => {
      if (!cancelled) setLoading(false);
    });
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [status, debouncedSearch, debouncedModel, sort, showUserCreated, focusSkinId, refreshToken, catalogVersion]);

  const total = liveries.length;
  const owned = useMemo(() => liveries.filter((item) => item.is_owned).length, [liveries]);
  const missing = total - owned;
  const completion = total > 0 ? Math.round((owned / total) * 100) : 0;

  // While a skin is pinned the grid is that one card. Any filter the operator
  // touches means they are done looking at it, so the pin releases and the full
  // collection comes back rather than filtering inside a single-card list.
  const focused = focusSkinId === null ? null : liveries.find((item) => item.skin_id === focusSkinId) ?? null;
  const visibleLiveries = focusSkinId === null ? liveries : liveries.filter((item) => item.skin_id === focusSkinId);
  const clearFocus = () => { setFocusSkinId(null); if (focusSkinId !== null) onPresetCleared(); };

  const activeFilters = [status !== "all", Boolean(debouncedSearch), Boolean(debouncedModel)].filter(Boolean).length;
  const clearFilters = () => { clearFocus(); setStatus("all"); setSearch(""); setModel(""); setSort(DEFAULT_SORT); };
  const syncCatalog = async () => {
    setSyncing(true);
    setSyncNotice("Reading booster packs, shop offers, and the active challenge. The first artwork sync can take a few minutes.");
    try {
      const result = await triggerSyncLiveries();
      setSyncNotice(result.message);
      setCatalogVersion((version) => version + 1);
    } catch (reason) {
      setSyncNotice(reason instanceof Error ? reason.message : "Livery catalog sync failed");
    } finally {
      setSyncing(false);
    }
  };

  return (
    <div className="livery-workspace">
      <section className="livery-stats" aria-label="Collection summary">
        <div className="stat-tile">
          <span><Palette size={13} /> Special liveries</span>
          <strong>{total}</strong>
          <small>in the catalog pool</small>
        </div>
        <div className="stat-tile tone-green">
          <span><CheckCircle2 size={13} /> Collected</span>
          <strong>{owned}</strong>
          <small>flying somewhere in fleet</small>
        </div>
        <div className="stat-tile">
          <span><XCircle size={13} /> Missing</span>
          <strong>{missing}</strong>
          <small>not yet unlocked</small>
        </div>
        <div className="stat-tile tone-cyan">
          <span><Award size={13} /> Completion</span>
          <strong>{completion}%</strong>
          <small>of the album filled</small>
        </div>
      </section>

      <section className="flat-section">
        <div className="progress-section-body">
          <div className="progress-meta">
            <span>Special livery album progress</span>
            <strong>{owned} of {total} collected ({completion}%)</strong>
          </div>
          <div className="progress-track"><i style={{ width: `${completion}%` }} /></div>
        </div>
      </section>

      <section className="livery-sync-panel" aria-label="Reward catalog sync">
        <div className="livery-sync-sources" aria-hidden="true">
          <span className="is-booster"><Package size={15} /></span>
          <span className="is-shop"><ShoppingBag size={15} /></span>
          <span className="is-challenge"><Trophy size={15} /></span>
        </div>
        <div className="livery-sync-copy">
          <strong>Refresh reward catalog</strong>
          <span>Sync booster-pack drops, shop skins, the active challenge, and missing artwork through the mobile connection.</span>
        </div>
        <Button className="primary-action" onClick={syncCatalog} disabled={syncing}>
          <RefreshCcw size={15} className={syncing ? "is-spinning" : ""} />
          {syncing ? "Syncing catalog" : "Sync reward skins"}
        </Button>
      </section>

      {syncNotice && (
        <div className="inline-notice" aria-live="polite">
          <span>{syncNotice}</span>
          {!syncing && <Button onClick={() => setSyncNotice(null)} aria-label="Dismiss notice"><X size={14} /></Button>}
        </div>
      )}

      {focusSkinId !== null && (
        <div className="inline-notice is-focus" aria-live="polite">
          <span>{focused ? <>Showing <b>{displayLiveryName(focused)}</b>, the livery you opened from the fleet page.</> : "That livery is not in the local catalog yet. Sync the reward skins or show the full collection."}</span>
          <Button onClick={clearFocus}>Show all liveries</Button>
        </div>
      )}

      <FilterBar className="livery-controls" active={activeFilters} onClear={clearFilters}>
        <SegmentedControl className="segmented-control" label="Ownership status" value={status} onChange={(next) => { clearFocus(); setStatus(next); }} options={[
          { value: "all", label: "All", className: "segmented-option" },
          { value: "owned", label: <><CheckCircle2 size={12} /> Owned</>, className: "segmented-option tone-green" },
          { value: "unowned", label: <><XCircle size={12} /> Missing</>, className: "segmented-option tone-amber" },
        ]} />
        <SearchInput value={search} onChange={(value) => { clearFocus(); setSearch(value); }} placeholder="Search livery or booster event" />
        <SearchInput value={model} onChange={(value) => { clearFocus(); setModel(value); }} placeholder="Model, e.g. 737 or A380" icon={Plane} />
        <MenuSelect label="Sort by" value={sort} onChange={(next) => setSort(next as SortMode)} options={SORT_OPTIONS} align="right" />
        <label className="livery-toggle" title="Player-designed market liveries (e.g. LH-A388)">
          <Checkbox checked={showUserCreated} onCheckedChange={(checked) => setShowUserCreated(checked === true)} />
          <Wand2 size={13} />
          <span>User-created</span>
        </label>
      </FilterBar>

      <div className="livery-grid">
        {loading ? Array.from({ length: 8 }).map((_, index) => <div className="livery-card-skeleton" key={index} />)
          : error ? (
            <ErrorState inline title="Couldn't load liveries" message={error} />
          ) : visibleLiveries.length === 0 ? (
            <EmptyState title="No liveries match" hint="Adjust or clear the active filters." />
          ) : visibleLiveries.map((item) => (
            <LiveryCard key={item.skin_id} item={item} onOpenModal={() => setModalItem(item)} onViewInFleet={onViewInFleet} />
          ))}
      </div>

      {modalItem && <AircraftListModal item={modalItem} onClose={() => setModalItem(null)} onViewInFleet={onViewInFleet} />}
    </div>
  );
}

// The server tags a livery from the feeds it has synced (boosters, the
// challenge ladder, shop offers, the duty free). A DB that predates those
// syncs still knows a challenge livery by its name, so that stays as the
// fallback — and a market livery is always taggable from `source` alone.
function liveryTags(item: LiveryItem): LiveryTag[] {
  const tags = item.tags ?? [];
  if (tags.length > 0) return tags;
  const fallback: LiveryTag[] = [];
  const challenge = challengeTag(item.name);
  if (challenge) {
    fallback.push({ kind: "challenge", label: challenge, title: `Awarded by the ${challenge} challenge` });
  }
  if (item.is_user_created) {
    fallback.push({ kind: "market", label: "User-created", title: "Player-designed livery sold on the livery market" });
  }
  return fallback;
}

function LiveryCard({ item, onOpenModal, onViewInFleet }: { item: LiveryItem; onOpenModal: () => void; onViewInFleet: (liveryName: string) => void }) {
  const visible = item.aircraft.slice(0, CHIP_LIMIT);
  const overflow = item.aircraft.length - visible.length;
  const tags = liveryTags(item);
  const added = addedLabel(item.first_seen);
  const addedDays = daysSinceAdded(item.first_seen);
  const isNew = addedDays !== null && addedDays <= NEW_WINDOW_DAYS;

  return (
    <article className={`livery-card${item.is_owned ? " is-owned" : ""}`}>
      <div className="livery-image">
        <img src={`/api/skin_image/${item.skin_id}`} alt={item.name} loading="lazy" />
        <span className={`livery-badge${item.is_owned ? " is-owned" : " is-missing"}`}>
          {item.is_owned ? <><CheckCircle2 size={11} /> Owned ({item.owned_count})</> : <><XCircle size={11} /> Not owned</>}
        </span>
      </div>

      <div className="livery-head">
        <h4 title={displayLiveryName(item)}>{displayLiveryName(item)}</h4>
        {(tags.length > 0 || isNew) && (
          <div className="livery-tags">
            {isNew && (
              <span className="livery-tag is-new" title={`Added to the local DB ${added}`}>
                <Sparkles size={10} />
                <span>{addedDays === 0 ? "New today" : `New (${addedDays}d)`}</span>
              </span>
            )}
            {tags.map((tag) => {
              const { cls, Icon } = TAG_STYLES[tag.kind] ?? TAG_STYLES.market;
              return (
                <span className={`livery-tag ${cls}`} key={`${tag.kind}-${tag.label}`} title={tag.title}>
                  <Icon size={10} />
                  <span>{tag.label}</span>
                </span>
              );
            })}
          </div>
        )}
        <p className="livery-added" title="When a scrape first stored this livery locally">
          <CalendarClock size={11} /><span>Added {added ?? "unknown"}</span>
        </p>
      </div>

      <div className="livery-fleet-box">
        <div className="livery-fleet-head">
          <span>Aircraft in fleet</span>
          <strong>{item.owned_count} active</strong>
        </div>
        {item.is_owned && item.aircraft.length > 0 ? (
          <div className="livery-chip-row">
            {visible.map((plane) => (
              <span className="livery-aircraft-chip" key={plane.aircraft_id}><Plane size={10} /><span>{plane.name}</span></span>
            ))}
            {overflow > 0 && <Button className="livery-more-button" onClick={onOpenModal}>+{overflow} more…</Button>}
          </div>
        ) : (
          <p className="livery-empty-fleet">Not currently flying on any aircraft.</p>
        )}
      </div>

      {item.is_owned ? (
        <Button className="livery-view-button is-block" onClick={() => onViewInFleet(item.name)}>
          <span>View in Fleet</span>
        </Button>
      ) : (
        <p className="livery-locked-note">Available via drops / auction</p>
      )}
    </article>
  );
}
