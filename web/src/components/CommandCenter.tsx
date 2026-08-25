import React from "react";
import {
  AlertTriangle,
  ArrowUpRight,
  CheckCircle2,
  ChevronRight,
  CircleDot,
  Clock3,
  Database,
  LockKeyhole,
  Plane,
  Radar,
  Route,
  Smartphone,
} from "lucide-react";
import { AppView } from "./AppShell";
import { CommandCenterSnapshot } from "../types";
import { hubLabel } from "../hubFlag";

interface CommandCenterProps {
  snapshot: CommandCenterSnapshot | null;
  loading: boolean;
  error: string | null;
  onNavigate: (view: AppView, preset?: string) => void;
}

const integer = new Intl.NumberFormat(undefined, { maximumFractionDigits: 0 });
const compactMoney = new Intl.NumberFormat(undefined, {
  style: "currency",
  currency: "USD",
  notation: "compact",
  maximumFractionDigits: 1,
});

function formatDate(value: string | null) {
  if (!value) return "Never";
  const date = new Date(`${value.replace(" ", "T")}Z`);
  return Number.isNaN(date.valueOf()) ? value : date.toLocaleString([], {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export function CommandCenter({ snapshot, loading, error, onNavigate }: CommandCenterProps) {
  if (loading && !snapshot) return <CommandCenterSkeleton />;
  if (error && !snapshot) {
    return <div className="fatal-state"><AlertTriangle size={22} /><div><strong>Command Center unavailable</strong><p>{error}</p></div></div>;
  }
  if (!snapshot) return null;

  const { portfolio, alerts, pipeline, data_health: health } = snapshot;
  const maxStage = Math.max(...pipeline.map((stage) => stage.count), 1);

  return (
    <div className="command-layout">
      <section className="command-main">
        <section className="portfolio-strip" aria-label="Portfolio summary">
          <div className="portfolio-lead">
            <span className="summary-label">Operating weekly</span>
            <strong>{compactMoney.format(portfolio.operating_weekly_rev)}</strong>
            <small>{integer.format(portfolio.active)} aircraft producing revenue</small>
          </div>
          <div className="summary-divider" />
          <div className="summary-stat">
            <span>Planned upside</span>
            <strong>{compactMoney.format(portfolio.planned_weekly_rev)}</strong>
          </div>
          <div className="summary-stat">
            <span>Utilization</span>
            <strong>{portfolio.avg_utilization}%</strong>
          </div>
          <button className="summary-stat is-action" onClick={() => onNavigate("fleet", "idle")}>
            <span>Idle capacity</span>
            <strong>{integer.format(portfolio.idle)}</strong>
            <ArrowUpRight size={16} />
          </button>
        </section>

        <section className="flat-section attention-section">
          <div className="section-title-row">
            <div>
              <p className="section-kicker">Execution queue</p>
              <h2>Needs attention</h2>
            </div>
            <span className="section-count">{alerts.length} signals</span>
          </div>

          <div className="attention-list">
            {alerts.length ? alerts.map((alert) => (
              <button
                className={`attention-row tone-${alert.tone}${alert.target !== "fleet" ? " is-static" : ""}`}
                key={alert.id}
                onClick={() => alert.target === "fleet" && onNavigate("fleet", alert.preset)}
                disabled={alert.target !== "fleet"}
              >
                <span className="attention-icon">
                  {alert.tone === "critical" ? <AlertTriangle size={18} /> : alert.tone === "warning" ? <CircleDot size={18} /> : <Radar size={18} />}
                </span>
                <span className="attention-copy"><strong>{alert.title}</strong><small>{alert.detail}</small></span>
                {alert.target === "fleet" ? <ChevronRight size={18} /> : <span className="signal-only">Signal</span>}
              </button>
            )) : (
              <div className="empty-positive"><CheckCircle2 size={20} /><span>No operational exceptions detected.</span></div>
            )}
          </div>
        </section>

        <section className="flat-section pipeline-section">
          <div className="section-title-row">
            <div>
              <p className="section-kicker">Circuit workflow</p>
              <h2>From plan to operating revenue</h2>
            </div>
          </div>
          <div className="pipeline-track">
            {pipeline.map((stage, index) => (
              <React.Fragment key={stage.key}>
                <div className={`pipeline-stage stage-${stage.key}`}>
                  <div className="pipeline-stage-top"><span>{stage.label}</span><strong>{stage.count}</strong></div>
                  <div className="pipeline-bar"><i style={{ width: `${Math.max(5, stage.count / maxStage * 100)}%` }} /></div>
                  <small>{compactMoney.format(stage.weekly_rev)} weekly</small>
                </div>
                {index < pipeline.length - 1 && <ChevronRight className="pipeline-arrow" size={20} />}
              </React.Fragment>
            ))}
          </div>
        </section>

        <section className="flat-section hub-section">
          <div className="section-title-row">
            <div>
              <p className="section-kicker">Network portfolio</p>
              <h2>Hub performance</h2>
            </div>
            <span className="section-count">Top {snapshot.hubs.length}</span>
          </div>
          <div className="hub-table" role="table" aria-label="Hub performance">
            <div className="hub-table-head" role="row">
              <span>Hub</span><span>Fleet</span><span>Idle</span><span>Utilization</span><span>Circuits</span><span>Weekly</span>
            </div>
            {snapshot.hubs.map((hub) => (
              <div className="hub-table-row" role="row" key={hub.hub_iata}>
                <strong>{hubLabel(hub.hub_iata, hub.country_code)}</strong>
                <span>{integer.format(hub.count)}</span>
                <span className={hub.idle ? "value-warn" : ""}>{integer.format(hub.idle)}</span>
                <span className="hub-util"><i style={{ width: `${hub.avg_utilization}%` }} /><small>{hub.avg_utilization}%</small></span>
                <span>{hub.circuits}</span>
                <span>{compactMoney.format(hub.weekly_rev)}</span>
              </div>
            ))}
          </div>
        </section>
      </section>

      <aside className="command-side">
        {!snapshot.status.browser_connected && (
          <section className="capability-panel" aria-label="Limited mode capabilities">
            <div className="capability-header">
              <div><p className="section-kicker">Connection</p><h2>Limited mode</h2></div>
              <Smartphone size={19} />
            </div>
            <div className="capability-list">
              <CapabilityRow
                icon={<CheckCircle2 size={17} />}
                label="Available now"
                detail="Command data, cached fleet, and livery collection"
                available
              />
              <CapabilityRow
                icon={<Smartphone size={17} />}
                label={snapshot.status.mobile_configured ? "Mobile connection" : "Mobile not configured"}
                detail={snapshot.status.mobile_configured
                  ? "Fleet sync and aircraft purchase dates"
                  : "Configure a mobile session to enable API reads"}
                available={snapshot.status.mobile_configured}
              />
              <CapabilityRow
                icon={<LockKeyhole size={17} />}
                label="Chrome required"
                detail="Route buying, fleet renaming, schedules, and pricing"
              />
            </div>
            <p className="capability-note">Select Limited mode in the header to link Chrome and enable web-only actions.</p>
          </section>
        )}

        <section className="health-panel">
          <div className="health-header">
            <div><p className="section-kicker">System</p><h2>Data health</h2></div>
            <span className={`health-mark ${snapshot.status.browser_connected ? "is-good" : "is-limited"}`} />
          </div>
          <div className="health-list">
            <HealthRow icon={<Clock3 size={17} />} label="Newest fleet record" value={formatDate(health.newest_fleet_record)} />
            <HealthRow icon={<Plane size={17} />} label="Stale aircraft" value={integer.format(health.stale_aircraft)} warning={health.stale_aircraft > 0} />
            <HealthRow icon={<Route size={17} />} label="Route coverage" value={`${integer.format(health.owned_routes)} / ${integer.format(health.routes)}`} />
            <HealthRow icon={<Radar size={17} />} label="Market watches" value={integer.format(health.market_watches)} />
            <HealthRow icon={<Database size={17} />} label="Oldest fleet record" value={formatDate(health.oldest_fleet_record)} muted />
          </div>
          <p className="health-note">Freshness is measured across every aircraft, not only the newest synchronized row.</p>
        </section>

        <section className="next-step-panel">
          <span className="next-step-icon"><Plane size={20} /></span>
          <p className="section-kicker">Suggested next move</p>
          <h3>Recover idle capacity</h3>
          <p>Start with the {integer.format(portfolio.idle)} aircraft currently producing no scheduled utilization.</p>
          <button onClick={() => onNavigate("fleet", "idle")}>Open idle fleet <ArrowUpRight size={15} /></button>
        </section>
      </aside>
    </div>
  );
}

function CapabilityRow({ icon, label, detail, available }: { icon: React.ReactNode; label: string; detail: string; available?: boolean }) {
  return <div className={`capability-row${available ? " is-available" : " is-locked"}`}><span>{icon}</span><div><strong>{label}</strong><small>{detail}</small></div></div>;
}

function HealthRow({ icon, label, value, warning, muted }: { icon: React.ReactNode; label: string; value: string; warning?: boolean; muted?: boolean }) {
  return <div className={`health-row${warning ? " is-warning" : ""}${muted ? " is-muted" : ""}`}><span>{icon}</span><div><small>{label}</small><strong>{value}</strong></div></div>;
}

function CommandCenterSkeleton() {
  return <div className="command-skeleton"><div className="skeleton-block is-wide" /><div className="skeleton-grid"><div className="skeleton-block" /><div className="skeleton-block" /></div><div className="skeleton-block is-tall" /></div>;
}
