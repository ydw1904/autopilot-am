import { useEffect, useMemo, useRef, useState } from "react";
import { Dices, Package, RefreshCcw, Sparkles } from "lucide-react";
import { fetchDailyLiveries, fetchPurchaseBackfillStatus, triggerSyncFleet } from "../api";
import type { PurchaseBackfillStatus } from "../api";
import { CommandCenterSnapshot, DailyLivery, FleetStats, HaulTab } from "../types";
import { clearDailyLiverySeed, readDailyLiverySeed, writeDailyLiverySeed } from "../dailyLiverySeed";
import { dateTime, integer } from "../format";
import { hubFlagMap, hubLabel } from "../hubFlag";
import { cleanLiveryName, splitLiveryName } from "../liveryName";
import { MenuOption } from "./MenuSelect";
import { AircraftEditor } from "./AircraftEditor";
import { AircraftArtwork, FleetBrowser } from "./FleetBrowser";
import { SegmentedControl } from "./SegmentedControl";
import { Button } from "@/components/ui/button";

interface FleetWorkspaceProps {
  snapshot: CommandCenterSnapshot | null;
  initialPreset?: string;
  refreshToken: number;
  onDataChanged: () => void;
  onOpenLivery: (skinId: number) => void;
  onOpenAircraft: (aircraftId: number) => void;
  onPresetCleared: () => void;
}

// A showcase card names at most this many hubs; the rest collapse into a "+N"
// chip whose tooltip spells them out, so a livery spread over eight bases
// cannot push the card taller than the two beside it.
const DAILY_HUB_CHIPS = 2;
const HAUL_TABS: { key: HaulTab; label: string }[] = [
  { key: "all", label: "All aircraft" },
  { key: "short", label: "Short haul" },
  { key: "medium", label: "Medium haul" },
  { key: "long", label: "Long haul" },
  { key: "cargo", label: "Cargo" },
];

export function FleetWorkspace({ snapshot, initialPreset, refreshToken, onDataChanged, onOpenLivery, onOpenAircraft, onPresetCleared }: FleetWorkspaceProps) {
  const [stats, setStats] = useState<FleetStats | null>(null);
  const [dailyLiveries, setDailyLiveries] = useState<DailyLivery[]>([]);
  const [rerolling, setRerolling] = useState(false);
  const [hub, setHub] = useState("all");
  const [haul, setHaul] = useState<HaulTab>("all");
  const [syncing, setSyncing] = useState(false);
  const [backfill, setBackfill] = useState<PurchaseBackfillStatus | null>(null);
  const [backfillWatch, setBackfillWatch] = useState(0);
  const [notice, setNotice] = useState<string | null>(null);
  // The parent re-creates onDataChanged every render; the poll loop must not
  // restart because of that.
  const dataChangedRef = useRef(onDataChanged);
  dataChangedRef.current = onDataChanged;

  useEffect(() => {
    let cancelled = false;
    const loadDailyLiveries = async (): Promise<DailyLivery[]> => {
      const stored = readDailyLiverySeed();
      if (!stored) return fetchDailyLiveries(3);
      const liveries = await fetchDailyLiveries(3, stored.seed);
      // A stored seed only stands for the day it was rolled on. Once the server
      // has moved to a new date, drop it so the day opens on its own three.
      if (liveries.length > 0 && liveries[0].day !== stored.day) {
        clearDailyLiverySeed();
        return fetchDailyLiveries(3);
      }
      return liveries;
    };
    loadDailyLiveries()
      .then((liveries) => { if (!cancelled) setDailyLiveries(liveries); })
      .catch(() => { if (!cancelled) setDailyLiveries([]); });
    return () => { cancelled = true; };
  }, [refreshToken]);

  // A fleet sync starts the purchase-date sweep on the server; follow it here so
  // the dates appear without a manual click, and reload the page once it lands.
  useEffect(() => {
    let cancelled = false;
    let timer = 0;
    let sawRunning = false;
    const poll = async () => {
      try {
        const status = await fetchPurchaseBackfillStatus();
        if (cancelled) return;
        setBackfill(status);
        if (status.running) {
          sawRunning = true;
          timer = window.setTimeout(poll, 3000);
        } else if (sawRunning) {
          if (status.message) setNotice(status.message);
          dataChangedRef.current();
        }
      } catch {
        /* the status endpoint is best-effort; the manual button still works */
      }
    };
    poll();
    return () => { cancelled = true; window.clearTimeout(timer); };
  }, [backfillWatch]);

  const haulCounts = stats?.hauls || snapshot?.fleet_facets.hauls;
  const hubOptions = useMemo<MenuOption[]>(() => [
    { value: "all", label: "All hubs" },
    ...(stats?.hubs || snapshot?.fleet_facets.hubs || []).map((item) => ({
      value: item.hub_iata,
      label: hubLabel(item.hub_iata, item.country_code),
      hint: `${item.count} aircraft`,
    })),
  ], [stats, snapshot]);
  const hubFlags = useMemo(() => hubFlagMap(stats?.hubs || snapshot?.fleet_facets.hubs), [stats, snapshot]);

  const syncFleet = async () => {
    setSyncing(true);
    setNotice("Reading the fleet from the mobile connection...");
    try {
      const result = await triggerSyncFleet(hub === "all" ? undefined : hub);
      setNotice(result.message);
      onDataChanged();
      if (result.purchase_backfill) {
        setBackfill(result.purchase_backfill);
        setBackfillWatch((value) => value + 1);
      }
    } catch (reason) {
      setNotice(reason instanceof Error ? reason.message : "Fleet sync failed");
    } finally {
      setSyncing(false);
    }
  };

  const rerollDailyLiveries = async () => {
    setRerolling(true);
    try {
      const seed = Date.now().toString(36);
      const liveries = await fetchDailyLiveries(3, seed);
      setDailyLiveries(liveries);
      // Keep the seed so leaving the page and coming back -- or a plain reload
      // -- shows the rerolled three rather than the day's default pick.
      if (liveries.length > 0) writeDailyLiverySeed({ day: liveries[0].day, seed });
    } catch {
      /* keep the current pick on failure */
    } finally {
      setRerolling(false);
    }
  };

  const summary = [
    { label: "Total fleet", value: stats?.total ?? snapshot?.portfolio.fleet_total, note: "aircraft", tone: "" },
    { label: "Active", value: stats?.active ?? snapshot?.portfolio.active, note: "scheduled", tone: "green" },
    { label: "Warehouse", value: stats?.idle ?? snapshot?.portfolio.idle, note: "idle", tone: "amber" },
    { label: "Avg utilization", value: stats ? `${stats.avg_utilization}%` : snapshot ? `${snapshot.portfolio.avg_utilization}%` : undefined, note: "fleet average", tone: "cyan" },
    { label: "Special liveries", value: stats?.special_skin_count, note: "aircraft", tone: "violet" },
  ];

  // "ac:<id>" swaps the browser for the single-aircraft editor; every other
  // preset ("idle", "livery:…") is a filter the browser applies itself.
  const editingId = initialPreset?.startsWith("ac:") ? Number(initialPreset.slice(3)) : 0;
  if (editingId) {
    return (
      <AircraftEditor
        aircraftId={editingId}
        snapshot={snapshot}
        onDataChanged={onDataChanged}
        onClose={onPresetCleared}
        onOpenAircraft={onOpenAircraft}
      />
    );
  }

  return (
    <div className="fleet-workspace">
      <section className="fleet-summary" aria-label="Fleet summary">
        {summary.map((item) => (
          <article className={`fleet-stat${item.tone ? ` tone-${item.tone}` : ""}`} key={item.label}>
            <span>{item.label}</span>
            <div><strong>{item.value === undefined ? "..." : typeof item.value === "number" ? integer.format(item.value) : item.value}</strong><small>{item.note}</small></div>
          </article>
        ))}
      </section>

      <section className="daily-liveries">
        <div className="fleet-section-heading">
          <div><h2><Sparkles size={16} /> Showcase</h2><p className="section-subtitle">Liveries of the day</p></div>
          <Button className="reroll-button" onClick={rerollDailyLiveries} disabled={rerolling} title="Reroll today's liveries">
            <Dices size={16} className={rerolling ? "is-spinning" : ""} />
            <span>Reroll</span>
          </Button>
        </div>
        <div className="daily-livery-grid">
          {dailyLiveries.length === 0 ? [0, 1, 2].map((index) => <div className="daily-livery-card is-placeholder" key={index} />) : dailyLiveries.map((livery) => {
            // The same livery usually flies from several bases, so the card
            // names each of them (with how many planes sit there) instead of
            // implying the whole batch lives at the sample aircraft's hub.
            const hubs = livery.hubs?.length
              ? livery.hubs
              : livery.sample_aircraft?.hub_iata
                ? [{ hub_iata: livery.sample_aircraft.hub_iata, count: livery.fleet_count }]
                : [];
            const hiddenHubs = hubs.slice(DAILY_HUB_CHIPS);
            return (
              <article className="daily-livery-card is-clickable" key={livery.skin_id} onClick={() => onOpenLivery(livery.skin_id)} title="Open this exact livery in the Liveries tab">
                <AircraftArtwork skinId={livery.skin_id} alt={livery.name} featured />
                <div className="daily-livery-copy">
                  <strong>{cleanLiveryName(livery.name)}</strong>
                  <small>{livery.sample_aircraft?.model || splitLiveryName(livery.name).model}</small>
                  <div>
                    <b>{livery.fleet_count} in fleet</b>
                    {hubs.slice(0, DAILY_HUB_CHIPS).map((hub) => (
                      <b key={hub.hub_iata} title={`${hub.count} at ${hubLabel(hub.hub_iata, hubFlags.get(hub.hub_iata))}`}>
                        {hubLabel(hub.hub_iata, hubFlags.get(hub.hub_iata))}
                        {hubs.length > 1 && <i>{hub.count}</i>}
                      </b>
                    ))}
                    {hiddenHubs.length > 0 && (
                      <b title={hiddenHubs.map((hub) => `${hubLabel(hub.hub_iata, hubFlags.get(hub.hub_iata))} (${hub.count})`).join(", ")}>+{hiddenHubs.length}</b>
                    )}
                  </div>
                </div>
              </article>
            );
          })}
        </div>
      </section>

      <SegmentedControl className="haul-tabs" label="Aircraft haul type" value={haul} onChange={setHaul} options={HAUL_TABS.map((tab) => ({
        value: tab.key,
        label: <>{tab.key === "cargo" && <Package size={15} />}<span>{tab.label}</span><small>{integer.format(haulCounts?.[tab.key] || 0)}</small></>,
      }))} />

      <FleetBrowser
        refreshToken={refreshToken}
        scope={{ haul }}
        preset={initialPreset}
        hub={hub}
        hubOptions={hubOptions}
        onHubChange={setHub}
        backfill={backfill}
        notice={notice}
        onNotice={setNotice}
        onOpenAircraft={onOpenAircraft}
        onStats={setStats}
        toolbarExtra={
          <Button className="primary-action" onClick={syncFleet} disabled={syncing}>
            <RefreshCcw size={16} className={syncing ? "is-spinning" : ""} />
            {syncing ? "Syncing" : hub === "all" ? "Sync fleet" : `Sync ${hub}`}
          </Button>
        }
        filterNotes={snapshot?.status.fleet_last_synced && <small>Last synced {dateTime(snapshot.status.fleet_last_synced)}</small>}
      />
    </div>
  );
}
