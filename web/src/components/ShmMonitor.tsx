import React, { useEffect, useState } from "react";
import {
  Activity,
  Clock3,
  Eye,
  Filter,
  Radar,
  Search,
  ShieldCheck,
  ShoppingBag,
  Tag,
  X,
} from "lucide-react";
import { fetchShmMonitor, updateShmWatch } from "../api";
import { ShmMonitorSnapshot } from "../types";
import { MenuOption, MenuSelect } from "./MenuSelect";
import { compareWatches, sourceBucket } from "./shmFilters";

const integer = new Intl.NumberFormat();

const SOURCE_OPTIONS: MenuOption[] = [
  { value: "all", label: "All sources" },
  { value: "shop-pack:auto", label: "Paid pack" },
  { value: "challenge:auto", label: "Challenge" },
  { value: "booster", label: "Booster" },
  { value: "manual", label: "Manual" },
];
const OWNERSHIP_OPTIONS: MenuOption[] = [
  { value: "all", label: "All ownership" },
  { value: "missing", label: "Missing", hint: "not in hangar" },
  { value: "owned", label: "Owned" },
];
const ARMED_OPTIONS: MenuOption[] = [
  { value: "all", label: "All watches" },
  { value: "armed", label: "Armed", hint: "will buy" },
  { value: "observing", label: "Observing", hint: "watch only" },
];
const SORT_OPTIONS: MenuOption[] = [
  { value: "priority", label: "Buying priority" },
  { value: "label", label: "Livery name" },
  { value: "cheapest", label: "Cheapest seen", hint: "low to high" },
  { value: "last_price", label: "Last price seen", hint: "low to high" },
  { value: "cap", label: "Price cap", hint: "high to low" },
  { value: "sightings", label: "Listings seen", hint: "most first" },
  { value: "last_seen", label: "Last seen", hint: "newest first" },
];

function money(value: number | null | undefined): string {
  if (value === null || value === undefined) return "None";
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

interface ShmMonitorProps {
  refreshToken: number;
}

export function ShmMonitor({ refreshToken }: ShmMonitorProps) {
  const [snapshot, setSnapshot] = useState<ShmMonitorSnapshot | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [updatingSkin, setUpdatingSkin] = useState<number | null>(null);
  const [liveryQuery, setLiveryQuery] = useState("");
  const [modelQuery, setModelQuery] = useState("");
  const [source, setSource] = useState("all");
  const [ownership, setOwnership] = useState("all");
  const [armedFilter, setArmedFilter] = useState("all");
  const [sort, setSort] = useState("priority");

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
        summary: patch.armed === undefined ? current.summary : {
          ...current.summary,
          armed_watches: current.summary.armed_watches + (patch.armed ? 1 : -1),
        },
        watches: current.watches.map((watch) => watch.skin_id !== skinId ? watch : {
          ...watch,
          ...(patch.armed === undefined ? {} : { armed: patch.armed ? 1 : 0 }),
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

  const toggleArm = (skinId: number, armed: boolean, isOwned: boolean) => {
    // Arming a livery already in the hangar spends real money on a duplicate,
    // so it takes a deliberate yes rather than a stray click.
    if (armed && isOwned && !window.confirm(
      "You already own this livery. Arm it anyway and buy another copy?")) return;
    void patchWatch(skinId, { armed }, "Failed to update watch arming");
  };

  const commitCap = (skinId: number, raw: string, current: number | null) => {
    const parsed = parseMoney(raw);
    if (parsed === undefined || parsed === current) return;
    void patchWatch(skinId, { max_price: parsed }, "Failed to update price cap");
  };

  const livery = liveryQuery.trim().toLowerCase();
  const model = modelQuery.trim().toLowerCase();
  const visibleWatches = watches
    .filter((watch) =>
      (!livery || watch.label.toLowerCase().includes(livery)
        || String(watch.skin_id).includes(livery))
      && (!model || String(watch.model_id ?? "").includes(model))
      && (source === "all" || sourceBucket(watch.source) === source)
      && (ownership === "all" || watch.is_owned === (ownership === "owned"))
      && (armedFilter === "all" || Boolean(watch.armed) === (armedFilter === "armed")))
    .sort((a, b) => compareWatches(sort, a, b));

  const activeFilters = [Boolean(livery), Boolean(model), source !== "all",
    ownership !== "all", armedFilter !== "all"].filter(Boolean).length;
  const clearFilters = () => {
    setLiveryQuery("");
    setModelQuery("");
    setSource("all");
    setOwnership("all");
    setArmedFilter("all");
  };

  return (
    <div className="shm-workspace">
      {error && <div className="inline-notice"><span>{error}</span></div>}

      <section className="shm-summary" aria-label="SHM watcher summary">
        <article className={`shm-stat${status.observing ? " is-live" : ""}`}>
          <Radar size={17} />
          <span>{status.observing ? "Observing" : "No recent activity"}</span>
          <strong>{relativeTime(status.last_activity)}</strong>
        </article>
        <article className="shm-stat">
          <Eye size={17} />
          <span>Active watches</span>
          <strong>{integer.format(summary.active_watches)}</strong>
        </article>
        <article className="shm-stat">
          <ShoppingBag size={17} />
          <span>Paid-pack targets</span>
          <strong>{integer.format(summary.paid_pack_watches)}</strong>
        </article>
        <article className="shm-stat">
          <ShieldCheck size={17} />
          <span>Armed orders</span>
          <strong>{integer.format(summary.armed_watches)}</strong>
        </article>
        <article className="shm-stat">
          <Activity size={17} />
          <span>Matched listings</span>
          <strong>{integer.format(summary.matched_sightings)}</strong>
        </article>
        <article className="shm-stat">
          <ShieldCheck size={17} />
          <span>Live buys today</span>
          <strong>{integer.format(summary.real_buys_today)}</strong>
        </article>
      </section>

      <section className="flat-section shm-section">
        <div className="section-title-row">
          <div>
            <p className="section-kicker">Standing orders</p>
            <h2>Watched liveries</h2>
          </div>
          <span className="section-count">{summary.watched_models} models</span>
        </div>
        <section className="fleet-controls shm-controls">
          <div className="fleet-toolbar">
            <label className="search-control">
              <Search size={17} />
              <input value={liveryQuery} onChange={(event) => setLiveryQuery(event.target.value)} placeholder="Search livery or skin id" />
              {liveryQuery && <button onClick={() => setLiveryQuery("")} aria-label="Clear search"><X size={15} /></button>}
            </label>
            <input className="model-input" value={modelQuery} onChange={(event) => setModelQuery(event.target.value)} placeholder="Model id" />
            <MenuSelect label="Source" value={source} onChange={setSource} options={SOURCE_OPTIONS} icon={Tag} />
            <MenuSelect label="Ownership" value={ownership} onChange={setOwnership} options={OWNERSHIP_OPTIONS} icon={ShoppingBag} />
            <MenuSelect label="Live buy" value={armedFilter} onChange={setArmedFilter} options={ARMED_OPTIONS} icon={ShieldCheck} />
            <MenuSelect label="Sort by" value={sort} onChange={setSort} options={SORT_OPTIONS} align="right" />
          </div>
          <div className="active-filter-row">
            <span><Filter size={14} /> {activeFilters ? `${activeFilters} active filters` : "No filters applied"}</span>
            {activeFilters > 0 && <button onClick={clearFilters}>Clear all</button>}
            <small>Showing {integer.format(visibleWatches.length)} of {integer.format(watches.length)}</small>
          </div>
        </section>
        <div className="shm-table-wrap">
          <table className="shm-table">
            <thead>
              <tr><th>Livery</th><th>Model</th><th>Source</th><th>Ownership</th><th>Live buy</th><th>Price cap</th><th>Cheapest seen</th><th>Last price seen</th><th>Listings</th><th>Last seen</th></tr>
            </thead>
            <tbody>
              {visibleWatches.map((watch) => (
                <tr key={watch.skin_id}>
                  <td>
                    <div className="shm-watch-name">
                      <span><img src={`/api/skin_image/${watch.skin_id}`} alt="" loading="lazy" /></span>
                      <div><strong>{watch.label}</strong><small>Skin {watch.skin_id}</small></div>
                    </div>
                  </td>
                  <td>{watch.model_id ?? "Unknown"}</td>
                  <td><span className={`shm-chip${watch.source === "shop-pack:auto" ? " is-pack" : ""}`}>{watch.source === "shop-pack:auto" ? "Paid pack" : watch.source === "challenge:auto" ? "Challenge" : watch.source || "Manual"}</span></td>
                  <td><span className={`shm-chip${watch.is_owned ? " is-owned" : ""}`}>{watch.is_owned ? `Owned (${watch.owned_count})` : "Missing"}</span></td>
                  <td>
                    <label className="livery-toggle shm-arm-toggle" title={watch.is_owned ? "You already own this livery — arming buys a duplicate" : "Buy at the listing price only while balance remains positive"}>
                      <input
                        type="checkbox"
                        checked={Boolean(watch.armed)}
                        disabled={updatingSkin === watch.skin_id}
                        onChange={(event) => toggleArm(watch.skin_id, event.target.checked, watch.is_owned)}
                      />
                      {watch.armed ? "Armed" : "Observe"}
                    </label>
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
                  <td>{money(watch.cheapest_seen)}</td>
                  <td>{money(watch.last_price_seen)}</td>
                  <td>{integer.format(watch.sightings)}</td>
                  <td>{relativeTime(watch.last_seen)}</td>
                </tr>
              ))}
              {visibleWatches.length === 0 && <tr><td colSpan={10} className="shm-empty">{watches.length ? "No watched livery matches those filters." : "No liveries are being watched."}</td></tr>}
            </tbody>
          </table>
        </div>
      </section>

      <div className="shm-columns">
        <section className="flat-section shm-section">
          <div className="section-title-row">
            <div><p className="section-kicker">Matches</p><h2>Recent watched listings</h2></div>
            <span className="section-count">Latest 30</span>
          </div>
          <div className="shm-feed">
            {sightings.map((item) => (
              <article key={item.auction_id}>
                <div><strong>{item.skin_name}</strong><small>Auction {item.auction_id} · model {item.model_id ?? "?"}</small></div>
                <div><strong>{money(item.bin_price)}</strong><small>{duration(item.time_left_s)} left · {item.bids || 0} bids</small></div>
                <time>{relativeTime(item.last_seen)}</time>
              </article>
            ))}
            {sightings.length === 0 && <p className="shm-empty">No watched livery has appeared yet.</p>}
          </div>
        </section>

        <section className="flat-section shm-section">
          <div className="section-title-row">
            <div><p className="section-kicker">Coverage</p><h2>Recent model checks</h2></div>
            <span className="section-count">Latest 20</span>
          </div>
          <div className="shm-feed is-checks">
            {checks.map((check) => (
              <article key={check.model_id}>
                <div><strong>Model {check.model_id}</strong><small>{integer.format(check.checks)} checks total</small></div>
                <span className={`shm-chip${check.truncated ? " is-warning" : ""}`}>{check.truncated ? "Truncated" : "Complete"}</span>
                <time>{relativeTime(check.last_checked)}</time>
              </article>
            ))}
            {checks.length === 0 && <p className="shm-empty">No model checks recorded yet.</p>}
          </div>
        </section>
      </div>

      <section className="flat-section shm-section">
        <div className="section-title-row">
          <div><p className="section-kicker">Audit trail</p><h2>Purchase decisions</h2></div>
          <span className="section-count">{summary.dry_runs_today} dry runs today</span>
        </div>
        <div className="shm-feed">
          {decisions.map((decision) => (
            <article key={decision.buy_id}>
              <div><strong>{decision.skin_name}</strong><small>Auction {decision.auction_id} · {decision.note || "No note"}</small></div>
              <div><strong>{money(decision.bin_price)}</strong><small>Estimated {money(decision.est_cost)}</small></div>
              <span className={`shm-chip${decision.dry_run ? "" : " is-live"}`}>{decision.dry_run ? "Dry run" : decision.confirmed ? "Confirmed" : "Live"}</span>
              <time><Clock3 size={11} /> {relativeTime(decision.bought_at)}</time>
            </article>
          ))}
          {decisions.length === 0 && <p className="shm-empty">No purchase decisions recorded.</p>}
        </div>
      </section>
    </div>
  );
}
