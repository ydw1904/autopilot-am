import { useEffect, useMemo, useState } from "react";
import {
  AlertTriangle,
  ArrowDown,
  ArrowUp,
  CheckCircle2,
  Layers,
  Lock,
  RefreshCcw,
  Wand2,
  X,
} from "lucide-react";
import { applyPricing, fetchPricing } from "../api";
import {
  CommandCenterSnapshot,
  PriceClass,
  PricingMode,
  PricingPlan,
  PricingPlanRoute,
  PricingRoute,
  PricingSnapshot,
} from "../types";
import { MenuSelect } from "./MenuSelect";

interface PricingProps {
  snapshot: CommandCenterSnapshot | null;
  refreshToken: number;
}

const integer = new Intl.NumberFormat(undefined, { maximumFractionDigits: 0 });
const compactMoney = new Intl.NumberFormat(undefined, {
  style: "currency",
  currency: "USD",
  notation: "compact",
  maximumFractionDigits: 1,
});

const CLASSES: { key: PriceClass; label: string }[] = [
  { key: "eco", label: "Eco" },
  { key: "bus", label: "Bus" },
  { key: "first", label: "First" },
  { key: "cargo", label: "Cargo" },
];

const MODE_HINT: Record<PricingMode, string> = {
  ideal: "Match the audit's recommended price on every class.",
  fill: "Price to sell the last seat: drives remaining demand toward zero.",
  percent: "Recommended price scaled by a percentage.",
};

/** How far a route's current price sits from the audit's recommendation, in
 *  percent. Eco carries the volume, so eco is the signal. */
function ecoGap(route: PricingRoute): number | null {
  const current = route.price.eco ?? 0;
  const target = route.audit_price.eco ?? 0;
  if (!current || !target) return null;
  return ((current - target) / target) * 100;
}

function isLocked(route: PricingRoute, now: Date): boolean {
  if (!route.locked_until) return false;
  const until = new Date(`${route.locked_until.replace(" ", "T")}Z`);
  return !Number.isNaN(until.valueOf()) && until > now;
}

/** Routes a dry run says would actually move — the exact set an apply sends. */
function changedRoutes(plan: PricingPlan | null): PricingPlanRoute[] {
  return (plan?.routes ?? []).filter((route) => route.status === "dry-run");
}

export function Pricing({ snapshot, refreshToken }: PricingProps) {
  const hubs = useMemo(() => (snapshot?.hubs ?? []).map((hub) => hub.hub_iata), [snapshot]);
  const [hub, setHub] = useState("");
  const [data, setData] = useState<PricingSnapshot | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [sort, setSort] = useState("gap");
  const [reload, setReload] = useState(0);

  const [mode, setMode] = useState<PricingMode>("ideal");
  const [pct, setPct] = useState(100);
  const [scope, setScope] = useState("all");
  const [plan, setPlan] = useState<PricingPlan | null>(null);
  const [busy, setBusy] = useState<"preview" | "apply" | null>(null);
  const [confirming, setConfirming] = useState(false);
  const [planError, setPlanError] = useState<string | null>(null);
  const [result, setResult] = useState<PricingPlan | null>(null);

  useEffect(() => { if (!hub && hubs.length) setHub(hubs[0]); }, [hubs, hub]);

  useEffect(() => {
    if (!hub) return;
    let live = true;
    setLoading(true);
    setError(null);
    fetchPricing(hub)
      .then((result) => { if (live) { setData(result); setLoading(false); } })
      .catch((reason) => {
        if (!live) return;
        setData(null);
        setError(reason instanceof Error ? reason.message : "Live pricing read failed");
        setLoading(false);
      });
    return () => { live = false; };
  }, [hub, refreshToken, reload]);

  // A plan is a snapshot of prices that have since been re-read, so any change
  // to the hub, the mode, or the scope invalidates it rather than leaving a
  // stale "apply 43 changes" button pointed at numbers nobody saw.
  const discardPlan = () => { setPlan(null); setConfirming(false); setPlanError(null); };
  useEffect(discardPlan, [hub, mode, pct, scope, reload]);

  const circuits = useMemo(() => Array.from(
    new Set((data?.routes ?? []).map((route) => route.circuit).filter(Boolean) as string[])
  ).sort(), [data]);

  const now = new Date();
  const planByIata = useMemo(() => new Map(
    (plan?.routes ?? []).map((route) => [route.iata, route])
  ), [plan]);

  const rows = useMemo(() => {
    const list = [...(data?.routes ?? [])];
    list.sort((a, b) => {
      if (sort === "revenue") return b.daily_revenue - a.daily_revenue;
      if (sort === "iata") return a.iata.localeCompare(b.iata);
      if (sort === "remaining") return sumClasses(b.remaining) - sumClasses(a.remaining);
      // "gap": the most underpriced route first, because that is the money
      // left on the table the pricer would fix next.
      return (ecoGap(a) ?? 0) - (ecoGap(b) ?? 0);
    });
    return list;
  }, [data, sort]);

  const underpriced = rows.filter((route) => (ecoGap(route) ?? 0) < -1).length;
  const lockedCount = rows.filter((route) => isLocked(route, now)).length;
  const pending = changedRoutes(plan);

  const runPreview = async () => {
    if (!hub) return;
    setBusy("preview");
    setPlanError(null);
    setResult(null);
    try {
      setPlan(await applyPricing(hub, {
        mode, pct, dry_run: true,
        circuit: scope === "all" ? undefined : scope,
      }));
      setConfirming(false);
    } catch (reason) {
      setPlan(null);
      setPlanError(reason instanceof Error ? reason.message : "Preview failed");
    } finally {
      setBusy(null);
    }
  };

  const runApply = async () => {
    if (!hub || !pending.length) return;
    setBusy("apply");
    setPlanError(null);
    try {
      // Send exactly the routes the preview listed — not the scope — so the
      // write can only touch what the operator actually saw.
      const done = await applyPricing(hub, {
        mode, pct, dry_run: false, routes: pending.map((route) => route.iata),
      });
      setResult(done);
      setPlan(null);
      setConfirming(false);
      setReload((value) => value + 1);
    } catch (reason) {
      setPlanError(reason instanceof Error ? reason.message : "Apply failed");
    } finally {
      setBusy(null);
    }
  };

  return (
    <div className="stack-workspace">
      <section className="portfolio-strip" aria-label="Pricing summary">
        <div className="portfolio-lead">
          <span className="summary-label">Daily revenue at current prices</span>
          <strong>{compactMoney.format(data?.daily_revenue ?? 0)}</strong>
          <small>{data ? `${data.routes.length} priced routes at ${data.hub_iata}` : "Pick a hub to read live prices"}</small>
        </div>
        <div className="summary-divider" />
        <div className={`summary-stat${underpriced ? " tone-amber" : ""}`}>
          <span>Below recommendation</span>
          <strong>{underpriced}</strong>
          <small>eco more than 1% under</small>
        </div>
        <div className="summary-stat">
          <span>On cooldown</span>
          <strong>{lockedCount}</strong>
          <small>24h price lock</small>
        </div>
        <div className="summary-stat">
          <span>Source</span>
          <strong>{data?.backend === "cdp" ? "Browser" : data ? "Mobile API" : "—"}</strong>
          <small>one request per hub</small>
        </div>
      </section>

      <section className="flat-section">
        <div className="section-title-row">
          <div>
            <p className="section-kicker">Repricing</p>
            <h2>Plan a price run</h2>
          </div>
          <div className="section-header-controls">
            <MenuSelect
              label="Mode"
              value={mode}
              onChange={(value) => setMode(value as PricingMode)}
              options={[{ value: "ideal", label: "Ideal" }, { value: "fill", label: "Fill" },
                { value: "percent", label: "Percent" }]}
              icon={Wand2}
            />
            {mode === "percent" && (
              <label className="pct-field">
                <span>%</span>
                <input
                  type="number"
                  min={25}
                  max={200}
                  value={pct}
                  onChange={(event) => setPct(Number(event.target.value))}
                />
              </label>
            )}
            <MenuSelect
              label="Scope"
              value={scope}
              onChange={setScope}
              options={[{ value: "all", label: "Whole hub" },
                ...circuits.map((name) => ({ value: name, label: name }))]}
              icon={Layers}
            />
            <button className="primary-action" onClick={runPreview} disabled={!hub || busy !== null}>
              <Wand2 size={15} className={busy === "preview" ? "is-spinning" : ""} />
              <span>{busy === "preview" ? "Previewing…" : "Preview"}</span>
            </button>
          </div>
        </div>

        <div className="plan-bar">
          <p className="plan-hint">{MODE_HINT[mode]}</p>
          {planError && <div className="plan-error"><AlertTriangle size={15} /><span>{planError}</span></div>}
          {result && (
            <div className="plan-result">
              <CheckCircle2 size={16} />
              <span>
                Wrote {result.applied} price{result.applied === 1 ? "" : "s"}
                {result.counts.fail ? `, ${result.counts.fail} refused` : ""}. Prices re-read below.
              </span>
              <button className="plan-dismiss" onClick={() => setResult(null)} aria-label="Dismiss"><X size={14} /></button>
            </div>
          )}
          {plan && (
            <div className="plan-summary">
              <div className="plan-counts">
                <PlanCount label="Would change" value={pending.length} tone="amber" />
                <PlanCount label="Already at target" value={plan.counts.skipped ?? 0} />
                <PlanCount label="On cooldown" value={plan.counts.cooldown ?? 0} />
                {plan.fill_revenue && (
                  <PlanCount
                    label="Daily revenue"
                    value={`${compactMoney.format(plan.fill_revenue.current)} → ${compactMoney.format(plan.fill_revenue.target)}`}
                    tone="good"
                  />
                )}
              </div>
              <div className="plan-actions">
                <button className="ghost-button" onClick={discardPlan} disabled={busy !== null}>Discard</button>
                {pending.length === 0 ? (
                  <span className="plan-none">Nothing to write</span>
                ) : confirming ? (
                  <button className="danger-action" onClick={runApply} disabled={busy !== null}>
                    {busy === "apply" ? "Writing…" : `Confirm — write ${pending.length} price${pending.length === 1 ? "" : "s"}`}
                  </button>
                ) : (
                  <button className="primary-action" onClick={() => setConfirming(true)} disabled={busy !== null}>
                    Apply {pending.length} change{pending.length === 1 ? "" : "s"}
                  </button>
                )}
              </div>
            </div>
          )}
        </div>
      </section>

      <section className="flat-section">
        <div className="section-title-row">
          <div>
            <p className="section-kicker">Live route pricing</p>
            <h2>Current vs recommended</h2>
          </div>
          <div className="section-header-controls">
            <MenuSelect
              label="Hub"
              value={hub}
              onChange={setHub}
              options={hubs.map((iata) => ({ value: iata, label: iata }))}
              icon={Layers}
            />
            <MenuSelect
              label="Sort by"
              value={sort}
              onChange={setSort}
              options={[{ value: "gap", label: "Price gap" }, { value: "revenue", label: "Daily revenue" },
                { value: "remaining", label: "Unsold demand" }, { value: "iata", label: "Destination" }]}
            />
            <button className="icon-action" onClick={() => setReload((value) => value + 1)} disabled={loading || !hub} title="Re-read live prices">
              <RefreshCcw size={16} className={loading ? "is-spinning" : ""} />
              <span>Reload</span>
            </button>
          </div>
        </div>

        {error ? (
          <div className="fatal-state is-inline"><AlertTriangle size={20} /><div><strong>No live prices</strong><p>{error}</p></div></div>
        ) : (
          <div className="grid-table-wrap">
            <table className="grid-table">
              <thead>
                <tr>
                  <th>Route</th><th>Circuit</th>
                  {CLASSES.map((cls) => <th key={cls.key} className="is-numeric">{cls.label}</th>)}
                  <th className="is-numeric">Eco gap</th>
                  <th className="is-numeric">Unsold</th>
                  <th className="is-numeric">Daily</th>
                  {plan && <th>Plan</th>}
                </tr>
              </thead>
              <tbody>
                {rows.map((route) => {
                  const gap = ecoGap(route);
                  const locked = isLocked(route, now);
                  const planned = planByIata.get(route.iata);
                  const willChange = planned?.status === "dry-run";
                  return (
                    <tr key={route.iata} className={`${locked ? "is-dormant" : ""}${willChange ? " is-planned-change" : ""}`}>
                      <td>
                        <strong>{route.iata}{locked && <Lock size={11} className="inline-lock" />}</strong>
                        <small>{route.name || ""}</small>
                      </td>
                      <td>{route.circuit ? <span className="status-tag is-planned">{route.circuit}</span> : <span className="is-dim">—</span>}</td>
                      {CLASSES.map((cls) => (
                        <td key={cls.key} className="is-numeric">
                          {integer.format(route.price[cls.key] ?? 0)}
                          <small className={willChange ? "value-target" : "is-dim"}>
                            {integer.format((willChange ? planned?.target?.[cls.key] : route.audit_price[cls.key]) ?? 0)}
                          </small>
                        </td>
                      ))}
                      <td className={`is-numeric ${gap === null ? "" : gap < -1 ? "value-warn" : gap > 1 ? "value-good" : ""}`}>
                        {gap === null ? "—" : (
                          <>
                            {gap < 0 ? <ArrowDown size={11} /> : <ArrowUp size={11} />}
                            {Math.abs(gap).toFixed(1)}%
                          </>
                        )}
                      </td>
                      <td className="is-numeric">{integer.format(sumClasses(route.remaining))}</td>
                      <td className="is-numeric">{compactMoney.format(route.daily_revenue)}</td>
                      {plan && (
                        <td>
                          {planned
                            ? <span className={`status-tag is-${planned.status.replace("-", "")}`} title={planned.detail}>{PLAN_LABEL[planned.status] ?? planned.status}</span>
                            : <span className="is-dim">out of scope</span>}
                        </td>
                      )}
                    </tr>
                  );
                })}
                {!rows.length && !loading && (
                  <tr><td colSpan={plan ? 10 : 9}><div className="empty-positive"><span>No priced routes at this hub.</span></div></td></tr>
                )}
              </tbody>
            </table>
          </div>
        )}
        <p className="table-footnote">
          The small number under each price is the audit's recommendation, already corrected for business and first;
          while a plan is open it shows that plan's target instead. Writes go through <code>mobile_pricer.price_hub</code>,
          which skips any route inside its 24h cooldown.
        </p>
      </section>
    </div>
  );
}

const PLAN_LABEL: Record<string, string> = {
  "dry-run": "Will change",
  skipped: "At target",
  cooldown: "Cooldown",
  ok: "Written",
  fail: "Refused",
};

function PlanCount({ label, value, tone }: { label: string; value: number | string; tone?: "amber" | "good" }) {
  return (
    <div className={`plan-count${tone ? ` tone-${tone}` : ""}`}>
      <span>{label}</span>
      <strong>{typeof value === "number" ? integer.format(value) : value}</strong>
    </div>
  );
}

function sumClasses(values: Partial<Record<PriceClass, number>>): number {
  return (values.eco ?? 0) + (values.bus ?? 0) + (values.first ?? 0) + (values.cargo ?? 0);
}
