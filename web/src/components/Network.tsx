import React, { useEffect, useMemo, useState } from "react";
import { AlertTriangle, ChevronRight, Layers, Plane, Route as RouteIcon } from "lucide-react";
import { fetchNetwork } from "../api";
import { NetworkCircuit, NetworkSnapshot } from "../types";
import { MenuSelect } from "./MenuSelect";

interface NetworkProps {
  refreshToken: number;
  onOpenFleet: (preset: string) => void;
}

const integer = new Intl.NumberFormat(undefined, { maximumFractionDigits: 0 });
const compactMoney = new Intl.NumberFormat(undefined, {
  style: "currency",
  currency: "USD",
  notation: "compact",
  maximumFractionDigits: 1,
});

const STATUS_LABEL: Record<string, string> = {
  planned: "Planned",
  bought: "Acquiring",
  completed: "Operating",
};

export function Network({ refreshToken, onOpenFleet }: NetworkProps) {
  const [snapshot, setSnapshot] = useState<NetworkSnapshot | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [hub, setHub] = useState("all");
  const [status, setStatus] = useState("all");
  const [expanded, setExpanded] = useState<string | null>(null);

  useEffect(() => {
    let live = true;
    fetchNetwork()
      .then((data) => live && setSnapshot(data))
      .catch((reason) => live && setError(reason instanceof Error ? reason.message : "Network load failed"));
    return () => { live = false; };
  }, [refreshToken]);

  const circuits = useMemo(() => (snapshot?.circuits ?? []).filter((circuit) =>
    (hub === "all" || circuit.hub_iata === hub) && (status === "all" || circuit.status === status)
  ), [snapshot, hub, status]);

  if (error) {
    return <div className="fatal-state"><AlertTriangle size={22} /><div><strong>Network unavailable</strong><p>{error}</p></div></div>;
  }
  if (!snapshot) return <div className="command-skeleton"><div className="skeleton-block is-wide" /><div className="skeleton-block is-tall" /></div>;

  const { totals } = snapshot;
  const hubOptions = [{ value: "all", label: "All hubs" },
    ...snapshot.hubs.map((item) => ({ value: item.hub_iata, label: item.hub_iata, hint: `${item.circuits}` }))];

  return (
    <div className="stack-workspace">
      <section className="portfolio-strip" aria-label="Network summary">
        <div className="portfolio-lead">
          <span className="summary-label">Operating weekly</span>
          <strong>{compactMoney.format(totals.operating_weekly_rev)}</strong>
          <small>{totals.operating} of {totals.circuits} circuits producing revenue</small>
        </div>
        <div className="summary-divider" />
        <div className="summary-stat">
          <span>Planned upside</span>
          <strong>{compactMoney.format(totals.planned_weekly_rev)}</strong>
          <small>{totals.planned} circuits</small>
        </div>
        <div className="summary-stat">
          <span>Routes owned</span>
          <strong>{integer.format(totals.routes_owned)}</strong>
          <small>of {integer.format(totals.routes_known)} scouted</small>
        </div>
        <div className={`summary-stat${totals.unscheduled_waves ? " tone-amber" : ""}`}>
          <span>Unscheduled waves</span>
          <strong>{integer.format(totals.unscheduled_waves)}</strong>
          <small>bought but not flying</small>
        </div>
      </section>

      <section className="flat-section">
        <div className="section-title-row">
          <div>
            <p className="section-kicker">Circuit portfolio</p>
            <h2>Circuits</h2>
          </div>
          <div className="section-header-controls">
            <MenuSelect label="Hub" value={hub} onChange={setHub} options={hubOptions} icon={Layers} />
            <MenuSelect
              label="Status"
              value={status}
              onChange={setStatus}
              options={[{ value: "all", label: "All" }, { value: "completed", label: "Operating" },
                { value: "bought", label: "Acquiring" }, { value: "planned", label: "Planned" }]}
              icon={RouteIcon}
            />
            <span className="section-count">{circuits.length} shown</span>
          </div>
        </div>

        <div className="grid-table-wrap">
          <table className="grid-table">
            <thead>
              <tr>
                <th>Circuit</th><th>Model</th><th>Status</th><th>Seats</th><th>Waves</th>
                <th>Aircraft</th><th>Routes</th><th>Weekly</th><th />
              </tr>
            </thead>
            <tbody>
              {circuits.map((circuit) => (
                <React.Fragment key={circuit.name}>
                  <tr
                    className={`is-clickable${expanded === circuit.name ? " is-open" : ""}`}
                    onClick={() => setExpanded(expanded === circuit.name ? null : circuit.name)}
                  >
                    <td><strong>{circuit.name}</strong><small>{circuit.hub_iata} · {circuit.total_hours || 0} h</small></td>
                    <td>{circuit.aircraft_model}</td>
                    <td><span className={`status-tag is-${circuit.status}`}>{STATUS_LABEL[circuit.status] ?? circuit.status}</span></td>
                    <td className="is-numeric">{seatSummary(circuit)}</td>
                    <td className="is-numeric">
                      {circuit.waves_scheduled}/{circuit.waves_bought}
                      <small>plan {circuit.waves}</small>
                    </td>
                    <td className="is-numeric">
                      {circuit.aircraft ? (
                        <button
                          className="link-value"
                          onClick={(event) => { event.stopPropagation(); onOpenFleet(`name:${circuit.name}`); }}
                        >
                          {integer.format(circuit.aircraft)}
                        </button>
                      ) : <span className="is-dim">0</span>}
                      {circuit.idle_aircraft ? <small className="value-warn">{circuit.idle_aircraft} idle</small> : null}
                    </td>
                    <td className="is-numeric">
                      {circuit.routes_owned}/{circuit.routes.length}
                    </td>
                    <td className="is-numeric">{compactMoney.format(circuit.weekly_rev)}</td>
                    <td><ChevronRight size={16} className={expanded === circuit.name ? "is-rotated" : ""} /></td>
                  </tr>
                  {expanded === circuit.name && (
                    <tr className="detail-row">
                      <td colSpan={9}><RouteList circuit={circuit} /></td>
                    </tr>
                  )}
                </React.Fragment>
              ))}
              {!circuits.length && (
                <tr><td colSpan={9}><div className="empty-positive"><Plane size={18} /><span>No circuits match this filter.</span></div></td></tr>
              )}
            </tbody>
          </table>
        </div>
      </section>

      <section className="flat-section">
        <div className="section-title-row">
          <div>
            <p className="section-kicker">Coverage</p>
            <h2>Routes by hub</h2>
          </div>
          <span className="section-count">{snapshot.hubs.length} hubs</span>
        </div>
        <div className="grid-table-wrap">
          <table className="grid-table">
            <thead>
              <tr><th>Hub</th><th>Circuits</th><th>Operating</th><th>Aircraft</th><th>Routes owned</th><th>Scouted</th><th>Weekly</th></tr>
            </thead>
            <tbody>
              {snapshot.hubs.map((item) => (
                <tr key={item.hub_iata}>
                  <td><strong>{item.hub_iata}</strong></td>
                  <td className="is-numeric">{item.circuits}</td>
                  <td className="is-numeric">{item.operating}</td>
                  <td className="is-numeric">{integer.format(item.aircraft)}</td>
                  <td className="is-numeric">{integer.format(item.routes_owned)}</td>
                  <td className="is-numeric is-dim">{integer.format(item.routes_known)}</td>
                  <td className="is-numeric">{compactMoney.format(item.weekly_rev)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>
    </div>
  );
}

function seatSummary(circuit: NetworkCircuit) {
  const { eco, bus, fir, cargo } = circuit.seats;
  return [eco, bus, fir, cargo].some(Boolean) ? `${eco}/${bus}/${fir}/${cargo}` : "—";
}

function RouteList({ circuit }: { circuit: NetworkCircuit }) {
  return (
    <div className="route-list">
      {circuit.routes.map((route) => (
        <div className={`route-chip${route.is_owned ? " is-owned" : ""}`} key={route.dest_iata}>
          <strong>{route.dest_iata}</strong>
          <small>{route.dest_name || ""}</small>
          <span>{route.distance_km ? `${integer.format(route.distance_km)} km` : "—"} · {route.flight_time_rt ?? "—"} h</span>
          <span className="route-demand">
            {integer.format(route.eco_demand || 0)} / {integer.format(route.bus_demand || 0)} / {integer.format(route.fir_demand || 0)} / {integer.format(route.cargo_demand || 0)}
          </span>
          {!route.is_owned && <em>not owned</em>}
        </div>
      ))}
      {!circuit.routes.length && <span className="is-dim">No routes stored for this circuit.</span>}
    </div>
  );
}
