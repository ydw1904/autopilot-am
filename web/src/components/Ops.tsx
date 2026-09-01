import React, { useEffect, useState } from "react";
import {
  AlertTriangle,
  CheckCircle2,
  Clock3,
  Database,
  Gift,
  PackageCheck,
  Plane,
} from "lucide-react";
import { fetchOps } from "../api";
import { OpsSnapshot } from "../types";

interface OpsProps {
  refreshToken: number;
}

const integer = new Intl.NumberFormat(undefined, { maximumFractionDigits: 0 });

function parseUtc(value: string | null): Date | null {
  if (!value) return null;
  const date = new Date(`${value.replace(" ", "T").split(".")[0]}Z`);
  return Number.isNaN(date.valueOf()) ? null : date;
}

function formatWhen(value: string | null): string {
  const date = parseUtc(value);
  return date ? date.toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" }) : "Never";
}

/** "in 42 min" / "ready" against the server's own clock, not the browser's —
 *  the two drift, and a delivery that reads "ready" early is a wasted poll. */
function countdown(finishAt: string | null, serverTime: string | null): string {
  const finish = parseUtc(finishAt);
  const now = parseUtc(serverTime) ?? new Date();
  if (!finish) return "—";
  const minutes = Math.round((finish.getTime() - now.getTime()) / 60000);
  if (minutes <= 0) return "ready to claim";
  if (minutes < 60) return `in ${minutes} min`;
  return `in ${Math.floor(minutes / 60)} h ${minutes % 60} min`;
}

function ageHours(value: string | null): number | null {
  const date = parseUtc(value);
  return date ? (Date.now() - date.getTime()) / 3600000 : null;
}

export function Ops({ refreshToken }: OpsProps) {
  const [snapshot, setSnapshot] = useState<OpsSnapshot | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let live = true;
    fetchOps()
      .then((data) => live && setSnapshot(data))
      .catch((reason) => live && setError(reason instanceof Error ? reason.message : "Operations load failed"));
    return () => { live = false; };
  }, [refreshToken]);

  if (error) {
    return <div className="fatal-state"><AlertTriangle size={22} /><div><strong>Operations unavailable</strong><p>{error}</p></div></div>;
  }
  if (!snapshot) return <div className="command-skeleton"><div className="skeleton-block is-wide" /><div className="skeleton-block is-tall" /></div>;

  const { deliveries, daily, freshness } = snapshot;
  const aircraftPending = deliveries.events.filter((event) => event.type === "aircraft").length;

  return (
    <div className="ops-layout">
      <section className="flat-section">
        <div className="section-title-row">
          <div>
            <p className="section-kicker">Waiting list</p>
            <h2>Deliveries in flight</h2>
          </div>
          <span className="section-count">{deliveries.events.length} pending</span>
        </div>

        {deliveries.error ? (
          <div className="fatal-state is-inline"><AlertTriangle size={20} /><div><strong>Mobile session unavailable</strong><p>{deliveries.error}</p></div></div>
        ) : deliveries.events.length ? (
          <div className="grid-table-wrap">
            <table className="grid-table">
              <thead><tr><th>Item</th><th>Type</th><th>Aircraft</th><th>Ready</th><th className="is-numeric">Skip cost</th></tr></thead>
              <tbody>
                {deliveries.events.map((event) => (
                  <tr key={event.event_id ?? `${event.type}-${event.aircraft_id}`}>
                    <td><strong>{event.label || "—"}</strong><small>{formatWhen(event.finish_at)}</small></td>
                    <td>{event.type || "—"}</td>
                    <td>{event.aircraft_id ?? <span className="is-dim">—</span>}</td>
                    <td>{countdown(event.finish_at, deliveries.server_time)}</td>
                    <td className="is-numeric">{event.am_coins_to_skip ? `${integer.format(event.am_coins_to_skip)} AM¢` : "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <div className="empty-positive"><CheckCircle2 size={20} /><span>Nothing in delivery. Every aircraft is landed and sellable.</span></div>
        )}
        <p className="table-footnote">
          A finished delivery still blocks the aircraft until it is claimed. Server clock: {deliveries.server_time || "unknown"}.
          {aircraftPending ? ` ${aircraftPending} aircraft waiting.` : ""}
        </p>
      </section>

      <aside className="ops-side">
        <section className="health-panel">
          <div className="health-header">
            <div><p className="section-kicker">Daily rewards</p><h2>Free to claim</h2></div>
            <Gift size={19} />
          </div>
          {daily.error ? (
            <p className="capability-note">{daily.error}</p>
          ) : (
            <div className="health-list">
              <HealthRow icon={<Gift size={17} />} label="Currency claims left"
                value={integer.format(daily.currency_claims ?? 0)} warning={(daily.currency_claims ?? 0) > 0} />
              <HealthRow icon={<PackageCheck size={17} />} label="Wheel spin"
                value={daily.wheel_available ? "Available" : "Used"} warning={daily.wheel_available} />
              <HealthRow icon={<Plane size={17} />} label="Slot games left"
                value={integer.format(daily.slot_games_left ?? 0)} warning={(daily.slot_games_left ?? 0) > 0} />
            </div>
          )}
          <p className="health-note">Claiming runs through the mobile daily tools; this panel only reports what is still on the table.</p>
        </section>

        <section className="health-panel">
          <div className="health-header">
            <div><p className="section-kicker">Cache</p><h2>Data freshness</h2></div>
            <Database size={19} />
          </div>
          <div className="health-list">
            {freshness.map((source) => {
              const hours = ageHours(source.newest);
              return (
                <HealthRow
                  key={source.table}
                  icon={<Clock3 size={17} />}
                  label={`${source.label} · ${integer.format(source.rows)} rows`}
                  value={formatWhen(source.newest)}
                  warning={hours !== null && hours > 48}
                />
              );
            })}
          </div>
          <p className="health-note">Anything older than two days is flagged; re-run that dataset's sync before trusting it.</p>
        </section>
      </aside>
    </div>
  );
}

function HealthRow({ icon, label, value, warning }: { icon: React.ReactNode; label: string; value: string; warning?: boolean }) {
  return <div className={`health-row${warning ? " is-warning" : ""}`}><span>{icon}</span><div><small>{label}</small><strong>{value}</strong></div></div>;
}
