import React, { useEffect, useState } from "react";
import {
  Activity,
  Clock3,
  Eye,
  Radar,
  ShieldCheck,
  ShoppingBag,
} from "lucide-react";
import { fetchShmMonitor, updateShmWatchArm } from "../api";
import { ShmMonitorSnapshot } from "../types";

const integer = new Intl.NumberFormat();

function money(value: number | null | undefined): string {
  if (value === null || value === undefined) return "None";
  if (value >= 1_000_000_000) return `$${(value / 1_000_000_000).toFixed(2)}B`;
  if (value >= 1_000_000) return `$${(value / 1_000_000).toFixed(0)}M`;
  return `$${integer.format(value)}`;
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

  const toggleArm = async (skinId: number, armed: boolean) => {
    setUpdatingSkin(skinId);
    try {
      await updateShmWatchArm(skinId, armed);
      setSnapshot((current) => current ? {
        ...current,
        summary: {
          ...current.summary,
          armed_watches: current.summary.armed_watches + (armed ? 1 : -1),
        },
        watches: current.watches.map((watch) =>
          watch.skin_id === skinId ? { ...watch, armed: armed ? 1 : 0 } : watch),
      } : current);
      setError(null);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Failed to update watch arming");
    } finally {
      setUpdatingSkin(null);
    }
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
        <div className="shm-table-wrap">
          <table className="shm-table">
            <thead>
              <tr><th>Livery</th><th>Model</th><th>Source</th><th>Ownership</th><th>Live buy</th><th>Price cap</th><th>Cheapest seen</th><th>Listings</th><th>Last seen</th></tr>
            </thead>
            <tbody>
              {watches.map((watch) => (
                <tr key={watch.skin_id} className={!watch.active ? "is-muted" : undefined}>
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
                    <label className="livery-toggle shm-arm-toggle" title={watch.is_owned ? "Owned liveries cannot be armed" : "Buy at the listing price only while balance remains positive"}>
                      <input
                        type="checkbox"
                        checked={Boolean(watch.armed)}
                        disabled={watch.is_owned || updatingSkin === watch.skin_id}
                        onChange={(event) => toggleArm(watch.skin_id, event.target.checked)}
                      />
                      {watch.armed ? "Armed" : "Observe"}
                    </label>
                  </td>
                  <td>{watch.max_price === null ? <span className="value-muted">No cap</span> : money(watch.max_price)}</td>
                  <td>{money(watch.cheapest_seen)}</td>
                  <td>{integer.format(watch.sightings)}</td>
                  <td>{relativeTime(watch.last_seen)}</td>
                </tr>
              ))}
              {watches.length === 0 && <tr><td colSpan={9} className="shm-empty">No liveries are being watched.</td></tr>}
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
