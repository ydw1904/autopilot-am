import React from "react";
import {
  CheckCircle2,
  Clock3,
  Database,
  Gift,
  PackageCheck,
  Plane,
} from "lucide-react";
import { fetchOps } from "../api";
import { dateTime, integer, parseGameDate } from "../format";
import { EmptyState, ErrorState, LoadingState } from "./PageStates";
import { useApi } from "../useApi";
import { SectionHeader } from "./SectionHeader";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";

interface OpsProps {
  refreshToken: number;
}


/** "in 42 min" / "ready" against the server's own clock, not the browser's —
 *  the two drift, and a delivery that reads "ready" early is a wasted poll. */
function countdown(finishAt: string | null, serverTime: string | null): string {
  const finish = parseGameDate(finishAt);
  const now = parseGameDate(serverTime) ?? new Date();
  if (!finish) return "—";
  const minutes = Math.round((finish.getTime() - now.getTime()) / 60000);
  if (minutes <= 0) return "ready to claim";
  if (minutes < 60) return `in ${minutes} min`;
  return `in ${Math.floor(minutes / 60)} h ${minutes % 60} min`;
}

function ageHours(value: string | null): number | null {
  const date = parseGameDate(value);
  return date ? (Date.now() - date.getTime()) / 3600000 : null;
}

export function Ops({ refreshToken }: OpsProps) {
  const { data: snapshot, error } = useApi(fetchOps, [refreshToken]);

  if (error) return <ErrorState title="Operations unavailable" message={error} />;
  if (!snapshot) return <LoadingState />;

  const { deliveries, daily, freshness } = snapshot;
  const aircraftPending = deliveries.events.filter((event) => event.type === "aircraft").length;

  return (
    <div className="ops-layout">
      <section className="flat-section">
        <SectionHeader kicker="Waiting list" title="Deliveries in flight" count={<>{deliveries.events.length} pending</>} />

        {deliveries.error ? (
          <ErrorState inline title="Mobile session unavailable" message={deliveries.error} />
        ) : deliveries.events.length ? (
          <div className="grid-table-wrap">
            <Table className="grid-table">
              <TableHeader><TableRow><TableHead>Item</TableHead><TableHead>Type</TableHead><TableHead>Aircraft</TableHead><TableHead>Ready</TableHead><TableHead className="is-numeric">Skip cost</TableHead></TableRow></TableHeader>
              <TableBody>
                {deliveries.events.map((event) => (
                  <TableRow key={event.event_id ?? `${event.type}-${event.aircraft_id}`}>
                    <TableCell><strong>{event.label || "—"}</strong><small>{dateTime(event.finish_at)}</small></TableCell>
                    <TableCell>{event.type || "—"}</TableCell>
                    <TableCell>{event.aircraft_id ?? <span className="is-dim">—</span>}</TableCell>
                    <TableCell>{countdown(event.finish_at, deliveries.server_time)}</TableCell>
                    <TableCell className="is-numeric">{event.am_coins_to_skip ? `${integer.format(event.am_coins_to_skip)} AM¢` : "—"}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
        ) : (
          <EmptyState positive icon={CheckCircle2} title="Nothing in delivery" hint="Every aircraft is landed and sellable." />
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
                  value={dateTime(source.newest)}
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
