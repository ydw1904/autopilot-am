import React, { useState, useEffect, useMemo } from "react";
import {
  Plane,
  Sparkles,
  LayoutGrid,
  List,
  Search,
  CheckSquare,
  Square,
  Edit3,
  Send,
  X,
  RefreshCw,
  Package,
  ArrowDownAZ,
  ArrowDownWideNarrow,
  ArrowUpAZ,
  ArrowUpNarrowWide,
  Building2,
  Gauge,
  Layers,
} from "lucide-react";
import { DailyLivery, FleetAircraft, FleetStats, HaulTab } from "../types";
import {
  fetchDailyLiveries,
  fetchFleet,
  triggerSyncFleet,
  bulkRenameAircraft,
  assignToCircuit,
} from "../api";
import { BulkRenameModal } from "./BulkRenameModal";
import { MenuOption, MenuSelect } from "./MenuSelect";
import { AssignCircuitModal } from "./AssignCircuitModal";

interface FleetManagementProps {
  stats?: FleetStats | null;
  onRefreshStats: () => void;
  initialSkinId?: number | null;
  onClearSkinFilter?: () => void;
}

/** Haul tabs mirror the game's own split of the aircraft shop (category 1-3 /
 *  4-6 / 7-10). Cargo cuts across all three, so it sits beside them. */
const HAUL_TABS: { key: HaulTab; label: string; hint: string }[] = [
  { key: "all", label: "All Aircraft", hint: "every plane" },
  { key: "short", label: "Short Haul", hint: "Cat 1–3" },
  { key: "medium", label: "Medium Haul", hint: "Cat 4–6" },
  { key: "long", label: "Long Haul", hint: "Cat 7–10" },
  { key: "cargo", label: "Cargo", hint: "freighters" },
];

const UTIL_OPTIONS: MenuOption[] = [
  { value: "all", label: "All utilization" },
  { value: "active", label: "Active", hint: "above 0%" },
  { value: "idle", label: "Idle only", hint: "0%" },
  { value: "full", label: "Full", hint: "100%" },
  { value: "partial", label: "Partial", hint: "under 100%" },
];

const SKIN_OPTIONS: MenuOption[] = [
  { value: "all", label: "All liveries" },
  { value: "special", label: "Special liveries only" },
  { value: "manufacturer", label: "Manufacturer only" },
];

const SORT_OPTIONS: MenuOption[] = [
  { value: "name", label: "Name", hint: "A to Z", icon: ArrowDownAZ },
  { value: "name_desc", label: "Name", hint: "Z to A", icon: ArrowUpAZ },
  { value: "util_desc", label: "Utilization", hint: "Busiest first", icon: ArrowDownWideNarrow },
  { value: "util_asc", label: "Utilization", hint: "Idlest first", icon: ArrowUpNarrowWide },
  { value: "wear_desc", label: "Wear", hint: "Most worn first", icon: ArrowDownWideNarrow },
  { value: "model", label: "Model", hint: "A to Z", icon: Plane },
];

const isSpecialLivery = (skinName?: string | null) =>
  !!skinName && !/manufacturer/i.test(skinName);

/** Every plane gets a livery box — special, manufacturer or unknown — so the
 *  tile footer reads the same way down the whole grid. */
function liveryBadge(ac: { skin_id?: number | null; skin_name?: string | null }) {
  if (isSpecialLivery(ac.skin_name)) {
    return {
      label: `★ ${ac.skin_name!.split(" - ").pop()}`,
      cls: "bg-[#7C5CBF]/15 border-[#7C5CBF]/30 text-[#7C5CBF] font-semibold",
    };
  }
  if (ac.skin_id) {
    return {
      label: "Mfg Livery",
      cls: "bg-[#F1EEE6] border-[#E5E1D6] text-[#8B877C]",
    };
  }
  return {
    label: "No Livery",
    cls: "bg-white border-[#E5E1D6] text-[#B6B1A4]",
  };
}

/** ISO 3166-1 alpha-2 -> regional-indicator flag, so no emoji are hardcoded. */
function flagEmoji(countryCode?: string | null): string {
  const cc = (countryCode || "").trim().toUpperCase();
  if (cc.length !== 2) return "";
  return String.fromCodePoint(
    ...[...cc].map((ch) => 0x1f1e6 + ch.charCodeAt(0) - 65)
  );
}

function haulLabel(ac: FleetAircraft): string | null {
  if (ac.is_cargo) return "Cargo";
  if (ac.haul === "short") return "Short";
  if (ac.haul === "medium") return "Medium";
  if (ac.haul === "long") return "Long";
  return null;
}

export const FleetManagement: React.FC<FleetManagementProps> = ({
  stats,
  onRefreshStats,
  initialSkinId,
  onClearSkinFilter,
}) => {
  const [aircraft, setAircraft] = useState<FleetAircraft[]>([]);
  const [dailyLiveries, setDailyLiveries] = useState<DailyLivery[]>([]);
  const [loading, setLoading] = useState(true);
  const [syncing, setSyncing] = useState(false);
  const [statusMsg, setStatusMsg] = useState<string | null>(null);

  // Filters
  const [haulTab, setHaulTab] = useState<HaulTab>("all");
  const [selectedHub, setSelectedHub] = useState<string>("ALL");
  const [searchName, setSearchName] = useState("");
  const [searchModel, setSearchModel] = useState("");
  const [utilFilter, setUtilFilter] = useState<string>("all");
  const [skinFilter, setSkinFilter] = useState<"all" | "special" | "manufacturer">("all");
  const [activeSkinId, setActiveSkinId] = useState<number | null>(initialSkinId || null);
  const [sortBy, setSortBy] = useState<string>("name");
  const [viewMode, setViewMode] = useState<"grid" | "table">("grid");

  // Selection
  const [selectedIds, setSelectedIds] = useState<Set<number>>(new Set());

  // Modals
  const [renameModalOpen, setRenameModalOpen] = useState(false);
  const [assignModalOpen, setAssignModalOpen] = useState(false);

  useEffect(() => {
    if (initialSkinId) {
      setActiveSkinId(initialSkinId);
    }
  }, [initialSkinId]);

  const loadData = async () => {
    setLoading(true);
    try {
      let min_u: number | undefined;
      let max_u: number | undefined;
      if (utilFilter === "idle") {
        min_u = 0;
        max_u = 0;
      } else if (utilFilter === "active") {
        min_u = 0.01;
        max_u = 100;
      } else if (utilFilter === "full") {
        min_u = 100;
        max_u = 100;
      } else if (utilFilter === "partial") {
        min_u = 0.01;
        max_u = 99.99;
      }

      const data = await fetchFleet({
        hubs: selectedHub !== "ALL" ? [selectedHub] : undefined,
        name_query: searchName.trim() || undefined,
        model_query: searchModel.trim() || undefined,
        min_util: min_u,
        max_util: max_u,
        skin_filter: skinFilter,
        skin_id: activeSkinId,
        haul: haulTab,
        sort_by: sortBy,
      });
      setAircraft(data);
    } catch (e: any) {
      setStatusMsg(`Failed to load fleet: ${e.message}`);
    } finally {
      setLoading(false);
    }
  };

  const loadDailyLiveries = async () => {
    try {
      setDailyLiveries(await fetchDailyLiveries(3));
    } catch (e) {
      console.error("Failed to load liveries of the day", e);
    }
  };

  useEffect(() => {
    loadData();
  }, [selectedHub, searchName, searchModel, utilFilter, skinFilter, activeSkinId, sortBy, haulTab]);

  useEffect(() => {
    loadDailyLiveries();
  }, []);

  const handleSync = async () => {
    setSyncing(true);
    setStatusMsg("Reading the fleet from the mobile connection…");
    try {
      const hubArg = selectedHub !== "ALL" ? selectedHub : undefined;
      const res = await triggerSyncFleet(hubArg);
      setStatusMsg(res.message);
      await loadData();
      await loadDailyLiveries();
      onRefreshStats();
    } catch (err: any) {
      setStatusMsg(`Sync error: ${err.message}`);
    } finally {
      setSyncing(false);
    }
  };

  const handleToggleSelect = (id: number) => {
    const next = new Set(selectedIds);
    if (next.has(id)) next.delete(id);
    else next.add(id);
    setSelectedIds(next);
  };

  const handleSelectAll = () => {
    if (selectedIds.size === aircraft.length) {
      setSelectedIds(new Set());
    } else {
      setSelectedIds(new Set(aircraft.map((a) => a.aircraft_id)));
    }
  };

  const handleBulkRename = async (prefix: string, addNumbering: boolean) => {
    const ids = Array.from(selectedIds);
    if (!ids.length) return;
    setStatusMsg(`Renaming ${ids.length} aircraft…`);
    const res = await bulkRenameAircraft(ids, prefix, addNumbering);
    setStatusMsg(`Renamed: ${res.ok} succeeded, ${res.failed} failed.`);
    setSelectedIds(new Set());
    await loadData();
  };

  const handleAssignCircuit = async (circuitCode: string) => {
    const ids = Array.from(selectedIds);
    if (!ids.length) return;
    setStatusMsg(`Assigning ${ids.length} aircraft to ${circuitCode}…`);
    const res = await assignToCircuit(ids, circuitCode);
    setStatusMsg(`Assigned: ${res.ok} succeeded, ${res.failed} failed.`);
    setSelectedIds(new Set());
    await loadData();
  };

  const clearFilters = () => {
    setSelectedHub("ALL");
    setSearchName("");
    setSearchModel("");
    setUtilFilter("all");
    setSkinFilter("all");
    setActiveSkinId(null);
    setSortBy("name");
    setHaulTab("all");
    if (onClearSkinFilter) onClearSkinFilter();
  };

  const haulCount = (key: HaulTab): number | undefined => stats?.hauls?.[key];

  const hubFlags = useMemo(() => {
    const map = new Map<string, string>();
    stats?.hubs.forEach((h) => map.set(h.hub_iata, flagEmoji(h.country_code)));
    return map;
  }, [stats]);

  const hubBadge = (iata: string) => {
    const flag = hubFlags.get(iata);
    return flag ? `${flag} ${iata}` : iata;
  };

  const hubOptions = useMemo<MenuOption[]>(() => [
    { value: "ALL", label: "All hubs", hint: stats ? `${stats.total} aircraft` : undefined },
    ...(stats?.hubs || []).map((h) => ({
      value: h.hub_iata,
      label: `${flagEmoji(h.country_code)} ${h.hub_iata}`.trim(),
      hint: `${h.count} aircraft`,
    })),
  ], [stats]);

  const activeSkinName = useMemo(() => {
    if (!activeSkinId) return null;
    const fromDaily = dailyLiveries.find((l) => l.skin_id === activeSkinId);
    if (fromDaily) return fromDaily.name.split(" - ").pop() || fromDaily.name;
    const fromFleet = aircraft.find((a) => a.skin_id === activeSkinId)?.skin_name;
    return fromFleet ? fromFleet.split(" - ").pop() || fromFleet : `#${activeSkinId}`;
  }, [activeSkinId, dailyLiveries, aircraft]);

  return (
    <div className="flex-1 flex flex-col overflow-hidden bg-[#F5F2EC]">
      <div className="flex-1 overflow-y-auto">
        <div className="p-6 space-y-5">
          {/* ── Fleet stats ─────────────────────────────────────────────── */}
          <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-5 gap-3">
            <div className="bg-[#FFFFFF]/80 backdrop-blur border border-[#E5E1D6]/80 rounded-xl p-3.5 flex flex-col justify-between hover:border-[#CFC9BA] transition">
              <span className="text-[10px] font-mono font-semibold text-[#8B877C] tracking-wider uppercase">
                Total Fleet
              </span>
              <div className="flex items-baseline gap-1 mt-1">
                <span className="text-2xl font-black text-[#0A1E3C] font-mono">
                  {stats?.total.toLocaleString() ?? "—"}
                </span>
                <span className="text-[10px] text-[#8B877C] font-mono">planes</span>
              </div>
            </div>

            <div className="bg-[#FFFFFF]/80 backdrop-blur border border-[#E5E1D6]/80 rounded-xl p-3.5 flex flex-col justify-between hover:border-[#1E7E46]/30 transition">
              <span className="text-[10px] font-mono font-semibold text-[#1E7E46] tracking-wider uppercase flex items-center gap-1.5">
                <span className="w-1.5 h-1.5 rounded-full bg-[#1E7E46]" />
                Active Fleet
              </span>
              <div className="flex items-baseline gap-1 mt-1">
                <span className="text-2xl font-black text-[#1E7E46] font-mono">
                  {stats?.active.toLocaleString() ?? "—"}
                </span>
                <span className="text-[10px] text-[#8B877C] font-mono">&gt;0% util</span>
              </div>
            </div>

            <div className="bg-[#FFFFFF]/80 backdrop-blur border border-[#E5E1D6]/80 rounded-xl p-3.5 flex flex-col justify-between hover:border-[#E8A800]/30 transition">
              <span className="text-[10px] font-mono font-semibold text-[#9E7600] tracking-wider uppercase flex items-center gap-1.5">
                <span className="w-1.5 h-1.5 rounded-full bg-[#FFAD00]" />
                Warehouse (Idle)
              </span>
              <div className="flex items-baseline gap-1 mt-1">
                <span className="text-2xl font-black text-[#9E7600] font-mono">
                  {stats?.idle.toLocaleString() ?? "—"}
                </span>
                <span className="text-[10px] text-[#8B877C] font-mono">0% util</span>
              </div>
            </div>

            <div className="bg-[#FFFFFF]/80 backdrop-blur border border-[#E5E1D6]/80 rounded-xl p-3.5 flex flex-col justify-between hover:border-[#05164D]/30 transition">
              <span className="text-[10px] font-mono font-semibold text-[#1D6FB8] tracking-wider uppercase">
                Avg Utilization
              </span>
              <div className="flex items-baseline gap-1 mt-1">
                <span className="text-2xl font-black text-[#1D6FB8] font-mono">
                  {stats?.avg_utilization ? `${stats.avg_utilization}%` : "—"}
                </span>
                <span className="text-[10px] text-[#8B877C] font-mono">fleet avg</span>
              </div>
            </div>

            <div className="bg-[#FFFFFF]/80 backdrop-blur border border-[#E5E1D6]/80 rounded-xl p-3.5 flex flex-col justify-between hover:border-[#7C5CBF]/30 transition">
              <span className="text-[10px] font-mono font-semibold text-[#7C5CBF] tracking-wider uppercase flex items-center gap-1.5">
                <Sparkles className="w-3 h-3" />
                Special Liveries
              </span>
              <div className="flex items-baseline gap-1 mt-1">
                <span className="text-2xl font-black text-[#7C5CBF] font-mono">
                  {stats?.special_skin_count.toLocaleString() ?? "—"}
                </span>
                <span className="text-[10px] text-[#8B877C] font-mono">custom</span>
              </div>
            </div>
          </div>

          {/* ── Liveries of the day ─────────────────────────────────────── */}
          <section>
            <div className="flex items-baseline justify-between mb-2">
              <h3 className="text-xs font-mono font-bold text-[#0A1E3C] uppercase tracking-wider flex items-center gap-2">
                <Sparkles className="w-3.5 h-3.5 text-[#7C5CBF]" />
                Liveries of the Day
              </h3>
              <span className="text-[10px] font-mono text-[#8B877C]">
                three from your own fleet · rotates daily
              </span>
            </div>

            <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
              {dailyLiveries.length === 0
                ? [0, 1, 2].map((i) => (
                    <div
                      key={i}
                      className="h-[104px] rounded-xl border border-dashed border-[#E5E1D6] bg-white/40"
                    />
                  ))
                : dailyLiveries.map((lv) => {
                    const isActive = activeSkinId === lv.skin_id;
                    return (
                      <button
                        key={lv.skin_id}
                        onClick={() => {
                          setActiveSkinId(isActive ? null : lv.skin_id);
                          if (!isActive) setHaulTab("all");
                        }}
                        className={`text-left bg-white/90 backdrop-blur rounded-xl border p-3 flex items-center gap-3 transition hover:-translate-y-0.5 ${
                          isActive
                            ? "border-[#7C5CBF]/60 ring-1 ring-[#7C5CBF]/30 shadow-md"
                            : "border-[#E5E1D6]/80 hover:border-[#CFC9BA]"
                        }`}
                        title={
                          isActive ? "Clear this livery filter" : "Filter the fleet by this livery"
                        }
                      >
                        <div className="w-28 h-16 rounded-lg bg-white border border-[#E5E1D6]/80 flex items-center justify-center overflow-hidden p-1 flex-shrink-0">
                          <img
                            src={`/api/skin_image/${lv.skin_id}`}
                            alt={lv.name}
                            className="max-w-full max-h-full object-contain filter drop-shadow"
                            loading="lazy"
                          />
                        </div>
                        <div className="min-w-0 flex-1">
                          <p className="text-xs font-bold text-[#0A1E3C] truncate">
                            {lv.name.split(" - ").pop()}
                          </p>
                          <p className="text-[10px] font-mono text-[#8B877C] truncate mt-0.5">
                            {lv.sample_aircraft?.model || lv.name.split(" - ")[0]}
                          </p>
                          <div className="flex items-center gap-1.5 mt-1.5">
                            <span className="px-1.5 py-0.5 rounded border bg-[#7C5CBF]/15 border-[#7C5CBF]/30 text-[#7C5CBF] text-[10px] font-mono font-semibold">
                              ★ {lv.fleet_count} in fleet
                            </span>
                            {lv.sample_aircraft && (
                              <span className="px-1.5 py-0.5 rounded border bg-[#05164D]/10 border-[#05164D]/20 text-[#1D6FB8] text-[10px] font-mono font-bold">
                                {hubBadge(lv.sample_aircraft.hub_iata)}
                              </span>
                            )}
                          </div>
                        </div>
                      </button>
                    );
                  })}
            </div>
          </section>

          {/* ── Haul tabs ───────────────────────────────────────────────── */}
          <div className="flex flex-wrap items-center gap-1.5 border-b border-[#E5E1D6] pb-2">
            {HAUL_TABS.map((tab) => {
              const active = haulTab === tab.key;
              const count = haulCount(tab.key);
              return (
                <button
                  key={tab.key}
                  onClick={() => setHaulTab(tab.key)}
                  className={`px-3.5 py-2 rounded-lg text-xs font-mono transition flex items-center gap-2 ${
                    active
                      ? "bg-[#05164D] text-white font-bold shadow-sm"
                      : "bg-white/70 border border-[#E5E1D6] text-[#4E4B43] hover:border-[#CFC9BA] hover:text-[#0A1E3C]"
                  }`}
                  title={tab.hint}
                >
                  {tab.key === "cargo" && <Package className="w-3.5 h-3.5" />}
                  <span>{tab.label}</span>
                  {count !== undefined && (
                    <span
                      className={`text-[10px] ${active ? "text-white/70" : "text-[#8B877C]"}`}
                    >
                      {count.toLocaleString()}
                    </span>
                  )}
                </button>
              );
            })}
          </div>

          {/* ── Filter toolbar (dropdowns) ──────────────────────────────── */}
          <div className="bg-[#FFFFFF]/60 backdrop-blur border border-[#E5E1D6]/80 rounded-xl p-3 flex flex-wrap gap-2.5 items-center justify-between">
            <div className="flex flex-wrap items-center gap-2 flex-1 min-w-[280px]">
              <MenuSelect label="Hub" value={selectedHub} onChange={setSelectedHub} options={hubOptions} icon={Building2} />
              <MenuSelect label="Status" value={utilFilter} onChange={setUtilFilter} options={UTIL_OPTIONS} icon={Gauge} />
              <MenuSelect label="Livery" value={skinFilter} onChange={(next) => setSkinFilter(next as typeof skinFilter)} options={SKIN_OPTIONS} icon={Layers} />
              <MenuSelect label="Sort by" value={sortBy} onChange={setSortBy} options={SORT_OPTIONS} />

              {/* Name search */}
              <div className="relative min-w-[170px] flex-1 max-w-[240px]">
                <Search className="w-3.5 h-3.5 text-[#8B877C] absolute left-3 top-1/2 -translate-y-1/2" />
                <input
                  type="text"
                  placeholder="Search plane or circuit…"
                  value={searchName}
                  onChange={(e) => setSearchName(e.target.value)}
                  className="w-full pl-8 pr-3 py-1.5 bg-white/80 border border-[#E5E1D6] rounded-lg text-xs text-[#0A1E3C] placeholder-slate-400 font-mono focus:outline-none focus:border-[#05164D]"
                />
              </div>

              {/* Model search */}
              <input
                type="text"
                placeholder="Model (e.g. 747)…"
                value={searchModel}
                onChange={(e) => setSearchModel(e.target.value)}
                className="w-32 px-3 py-1.5 bg-white/80 border border-[#E5E1D6] rounded-lg text-xs text-[#0A1E3C] placeholder-slate-400 font-mono focus:outline-none focus:border-[#05164D]"
              />

              {activeSkinId && (
                <div className="flex items-center gap-1.5 px-2.5 py-1 rounded-lg bg-[#7C5CBF]/10 border border-[#7C5CBF]/30 text-[#7C5CBF] text-xs font-mono">
                  <span className="truncate max-w-[160px]">★ {activeSkinName}</span>
                  <button
                    onClick={() => {
                      setActiveSkinId(null);
                      if (onClearSkinFilter) onClearSkinFilter();
                    }}
                    className="hover:text-[#0A1E3C]"
                  >
                    <X className="w-3 h-3" />
                  </button>
                </div>
              )}

              <button
                onClick={clearFilters}
                className="px-2.5 py-1.5 text-xs text-[#8B877C] hover:text-[#0A1E3C] font-mono hover:bg-[#F1EEE6] rounded-lg transition"
              >
                Reset
              </button>
            </div>

            {/* View toggle & sync */}
            <div className="flex items-center gap-2">
              <div className="flex items-center bg-white border border-[#E5E1D6] rounded-lg p-0.5">
                <button
                  onClick={() => setViewMode("grid")}
                  className={`p-1.5 rounded-md text-xs transition ${
                    viewMode === "grid"
                      ? "bg-[#05164D]/20 text-[#1D6FB8]"
                      : "text-[#8B877C] hover:text-[#0A1E3C]"
                  }`}
                  title="Grid view"
                >
                  <LayoutGrid className="w-4 h-4" />
                </button>
                <button
                  onClick={() => setViewMode("table")}
                  className={`p-1.5 rounded-md text-xs transition ${
                    viewMode === "table"
                      ? "bg-[#05164D]/20 text-[#1D6FB8]"
                      : "text-[#8B877C] hover:text-[#0A1E3C]"
                  }`}
                  title="List view"
                >
                  <List className="w-4 h-4" />
                </button>
              </div>

              <button
                onClick={handleSync}
                disabled={syncing}
                className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-semibold font-mono transition ${
                  syncing
                    ? "bg-[#05164D]/20 text-[#1D6FB8] border border-[#05164D]/40"
                    : "bg-[#05164D] hover:bg-[#FFAD00] text-white font-bold shadow-sm"
                }`}
              >
                <RefreshCw className={`w-3.5 h-3.5 ${syncing ? "animate-spin" : ""}`} />
                <span>{syncing ? "Syncing…" : "Sync Fleet"}</span>
              </button>
            </div>
          </div>

          {statusMsg && (
            <div className="text-[11px] font-mono text-[#4E4B43] bg-white/70 border border-[#E5E1D6] rounded-lg px-3 py-2 flex items-center justify-between">
              <span>{statusMsg}</span>
              <button onClick={() => setStatusMsg(null)} className="text-[#8B877C] hover:text-[#0A1E3C]">
                <X className="w-3 h-3" />
              </button>
            </div>
          )}

          {/* ── Selection bar ───────────────────────────────────────────── */}
          {aircraft.length > 0 && (
            <div className="flex items-center justify-between text-xs text-[#8B877C] px-1 font-mono">
              <div className="flex items-center gap-3">
                <button
                  onClick={handleSelectAll}
                  className="flex items-center gap-1.5 hover:text-[#0A1E3C] text-[#4E4B43] font-medium"
                >
                  {selectedIds.size === aircraft.length && aircraft.length > 0 ? (
                    <CheckSquare className="w-4 h-4 text-[#1D6FB8]" />
                  ) : (
                    <Square className="w-4 h-4 text-[#8B877C]" />
                  )}
                  <span>
                    {selectedIds.size > 0
                      ? `${selectedIds.size} of ${aircraft.length} selected`
                      : "Select All"}
                  </span>
                </button>

                {selectedIds.size > 0 && (
                  <div className="flex items-center gap-2 pl-3 border-l border-[#E5E1D6]">
                    <button
                      onClick={() => setRenameModalOpen(true)}
                      className="flex items-center gap-1 px-2.5 py-1 rounded bg-[#F1EEE6] hover:bg-[#E5E1D6] text-[#4E4B43] font-medium transition"
                    >
                      <Edit3 className="w-3 h-3 text-[#1D6FB8]" />
                      <span>Rename</span>
                    </button>
                    <button
                      onClick={() => setAssignModalOpen(true)}
                      className="flex items-center gap-1 px-2.5 py-1 rounded bg-[#F1EEE6] hover:bg-[#E5E1D6] text-[#4E4B43] font-medium transition"
                    >
                      <Send className="w-3 h-3 text-[#1E7E46]" />
                      <span>Assign Circuit</span>
                    </button>
                  </div>
                )}
              </div>

              <div className="flex items-center gap-3">
                {haulTab === "all" && !!stats?.hauls?.unknown && (
                  <span className="text-[10px] text-[#B6B1A4]">
                    {stats.hauls.unknown} without spec data (All tab only)
                  </span>
                )}
                <span>Showing {aircraft.length.toLocaleString()} aircraft</span>
              </div>
            </div>
          )}

          {/* ── Aircraft ────────────────────────────────────────────────── */}
          {loading ? (
            <div className="h-64 flex flex-col items-center justify-center gap-3 text-[#8B877C] font-mono">
              <RefreshCw className="w-6 h-6 animate-spin text-[#1D6FB8]" />
              <span>Loading fleet data…</span>
            </div>
          ) : aircraft.length === 0 ? (
            <div className="h-64 flex flex-col items-center justify-center gap-2 text-[#8B877C] font-mono">
              <Plane className="w-8 h-8 opacity-40 text-[#B6B1A4]" />
              <p className="text-sm">No aircraft match current filters.</p>
              <button onClick={clearFilters} className="text-xs text-[#1D6FB8] underline mt-1">
                Clear filters
              </button>
            </div>
          ) : viewMode === "grid" ? (
            <div className="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-3 xl:grid-cols-4 gap-3.5 pb-12">
              {aircraft.map((ac) => {
                const isSelected = selectedIds.has(ac.aircraft_id);
                const badge = liveryBadge(ac);
                const utilColor =
                  ac.utilization >= 100
                    ? "bg-[#1E7E46]"
                    : ac.utilization > 0
                    ? "bg-[#FFAD00]"
                    : "bg-[#CFC9BA]";

                return (
                  <div
                    key={ac.aircraft_id}
                    onClick={() => handleToggleSelect(ac.aircraft_id)}
                    className={`relative bg-[#FFFFFF]/90 backdrop-blur rounded-xl border p-4 flex flex-col justify-between gap-3 cursor-pointer transition-all duration-150 group hover:-translate-y-0.5 ${
                      isSelected
                        ? "border-[#05164D]/60 bg-[#F8F6F1]/90 shadow-md shadow-[#05164D]/10 ring-1 ring-[#05164D]/30"
                        : "border-[#E5E1D6]/80 hover:border-[#CFC9BA]"
                    }`}
                  >
                    {/* Header: checkbox + name + hub */}
                    <div className="flex items-start justify-between gap-2">
                      <div className="flex items-center gap-2 min-w-0">
                        <div
                          className={`w-4 h-4 rounded flex items-center justify-center transition ${
                            isSelected ? "bg-[#05164D] text-white" : "border border-[#CFC9BA] bg-white"
                          }`}
                        >
                          {isSelected && <CheckSquare className="w-3 h-3 fill-current" />}
                        </div>
                        <h4 className="text-xs font-mono font-bold text-[#0A1E3C] truncate group-hover:text-[#1D6FB8] transition">
                          {ac.name}
                        </h4>
                      </div>
                      <div className="flex items-center gap-1.5 flex-shrink-0">
                        {!!ac.is_cargo && (
                          <span className="px-1.5 py-0.5 rounded border bg-[#F1EEE6] border-[#E5E1D6] text-[#8B877C] text-[10px] font-mono">
                            Cargo
                          </span>
                        )}
                        <span className="px-2 py-0.5 rounded bg-[#05164D]/10 border border-[#05164D]/20 text-[#1D6FB8] text-[10px] font-mono font-bold">
                          {hubBadge(ac.hub_iata)}
                        </span>
                      </div>
                    </div>

                    {/* Artwork & specs */}
                    <div className="flex items-center gap-3">
                      <div className="w-24 h-14 rounded-lg bg-white border border-[#E5E1D6]/80 flex items-center justify-center overflow-hidden p-1 flex-shrink-0">
                        {ac.skin_id ? (
                          <img
                            src={`/api/skin_image/${ac.skin_id}`}
                            alt={ac.model}
                            className="max-w-full max-h-full object-contain filter drop-shadow"
                            loading="lazy"
                          />
                        ) : (
                          <Plane className="w-6 h-6 text-[#4E4B43] transform -rotate-45" />
                        )}
                      </div>
                      <div className="min-w-0 flex-1">
                        <p className="text-xs font-bold text-[#0A1E3C] truncate">{ac.model}</p>
                        <p className="text-[10px] font-mono text-[#8B877C] mt-0.5 truncate">
                          {ac.category ? `Cat ${ac.category}` : "Commercial"}
                          {ac.haul ? ` ${ac.haul}` : ""}
                        </p>
                        {ac.is_cargo ? (
                          <p className="text-[10px] font-mono text-[#8B877C] mt-0.5">
                            {ac.payload_t ?? ac.max_tonnage ?? 0} t payload
                          </p>
                        ) : (
                          ac.seats_eco !== undefined &&
                          ac.seats_eco !== null && (
                            <p className="text-[10px] font-mono text-[#8B877C] mt-0.5">
                              {ac.seats_eco}E / {ac.seats_bus || 0}B / {ac.seats_first || 0}F
                            </p>
                          )
                        )}
                      </div>
                    </div>

                    {/* Utilization */}
                    <div>
                      <div className="flex justify-between items-center text-[10px] font-mono mb-1">
                        <span className="text-[#8B877C]">UTILIZATION</span>
                        <span
                          className={`font-bold ${
                            ac.utilization >= 100
                              ? "text-[#1E7E46]"
                              : ac.utilization > 0
                              ? "text-[#1D6FB8]"
                              : "text-[#8B877C]"
                          }`}
                        >
                          {ac.utilization.toFixed(0)}%
                        </span>
                      </div>
                      <div className="w-full h-1.5 bg-white rounded-full overflow-hidden border border-[#E5E1D6]/60">
                        <div
                          className={`h-full rounded-full transition-all duration-300 ${utilColor}`}
                          style={{ width: `${Math.min(100, Math.max(0, ac.utilization))}%` }}
                        />
                      </div>
                    </div>

                    {/* Footer: livery box (always) + haul box */}
                    <div className="flex items-center justify-between gap-2 pt-2 border-t border-[#E5E1D6]/50 text-[10px] font-mono">
                      <span className={`px-1.5 py-0.5 rounded border truncate min-w-0 ${badge.cls}`}>
                        {badge.label}
                      </span>
                    </div>
                  </div>
                );
              })}
            </div>
          ) : (
            /* List view */
            <div className="bg-[#FFFFFF]/80 backdrop-blur border border-[#E5E1D6]/80 rounded-xl overflow-hidden mb-12">
              <div className="overflow-x-auto">
                <table className="w-full text-left text-xs font-mono">
                  <thead className="bg-white border-b border-[#E5E1D6] text-[#8B877C] uppercase text-[10px]">
                    <tr>
                      <th className="p-3 w-8">
                        <input
                          type="checkbox"
                          checked={selectedIds.size === aircraft.length && aircraft.length > 0}
                          onChange={handleSelectAll}
                          className="rounded bg-[#F8F6F1] border-[#CFC9BA]"
                        />
                      </th>
                      <th className="p-3">Aircraft</th>
                      <th className="p-3">Model</th>
                      <th className="p-3">Hub</th>
                      <th className="p-3">Haul</th>
                      <th className="p-3">Utilization</th>
                      <th className="p-3">Seating</th>
                      <th className="p-3">Livery</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-[#E5E1D6]/70">
                    {aircraft.map((ac) => {
                      const isSelected = selectedIds.has(ac.aircraft_id);
                      const badge = liveryBadge(ac);
                      const haul = haulLabel(ac);
                      return (
                        <tr
                          key={ac.aircraft_id}
                          onClick={() => handleToggleSelect(ac.aircraft_id)}
                          className={`hover:bg-[#F1EEE6]/30 cursor-pointer transition ${
                            isSelected ? "bg-[#05164D]/10" : ""
                          }`}
                        >
                          <td className="p-3">
                            <input
                              type="checkbox"
                              checked={isSelected}
                              onChange={() => {}}
                              className="rounded bg-[#F8F6F1] border-[#CFC9BA]"
                            />
                          </td>
                          <td className="p-3 font-bold text-[#0A1E3C]">
                            <div className="flex items-center gap-2.5">
                              <div className="w-9 h-6 rounded bg-white border border-[#E5E1D6] flex items-center justify-center overflow-hidden flex-shrink-0">
                                {ac.skin_id ? (
                                  <img
                                    src={`/api/skin_image/${ac.skin_id}`}
                                    alt=""
                                    className="max-w-full max-h-full object-contain"
                                  />
                                ) : (
                                  <Plane className="w-3.5 h-3.5 text-[#8B877C]" />
                                )}
                              </div>
                              <span>{ac.name}</span>
                            </div>
                          </td>
                          <td className="p-3 text-[#4E4B43]">{ac.model}</td>
                          <td className="p-3">
                            <span className="px-2 py-0.5 rounded bg-[#05164D]/10 border border-[#05164D]/20 text-[#1D6FB8] font-bold">
                              {hubBadge(ac.hub_iata)}
                            </span>
                          </td>
                          <td className="p-3">
                            <span className="px-2 py-0.5 rounded border bg-[#F1EEE6] border-[#E5E1D6] text-[#8B877C]">
                              {haul || "Unknown"}
                            </span>
                          </td>
                          <td className="p-3">
                            <span
                              className={`font-bold ${
                                ac.utilization >= 100
                                  ? "text-[#1E7E46]"
                                  : ac.utilization > 0
                                  ? "text-[#1D6FB8]"
                                  : "text-[#8B877C]"
                              }`}
                            >
                              {ac.utilization.toFixed(0)}%
                            </span>
                          </td>
                          <td className="p-3 text-[#8B877C]">
                            {ac.is_cargo
                              ? `${ac.payload_t ?? ac.max_tonnage ?? 0} t`
                              : ac.seats_eco !== null && ac.seats_eco !== undefined
                              ? `${ac.seats_eco}E / ${ac.seats_bus || 0}B / ${ac.seats_first || 0}F`
                              : "Standard"}
                          </td>
                          <td className="p-3">
                            <span className={`px-2 py-0.5 rounded border ${badge.cls}`}>
                              {badge.label}
                            </span>
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            </div>
          )}
        </div>
      </div>

      {/* Modals */}
      <BulkRenameModal
        isOpen={renameModalOpen}
        onClose={() => setRenameModalOpen(false)}
        selectedCount={selectedIds.size}
        onRename={handleBulkRename}
      />
      <AssignCircuitModal
        isOpen={assignModalOpen}
        onClose={() => setAssignModalOpen(false)}
        selectedCount={selectedIds.size}
        onAssign={handleAssignCircuit}
      />
    </div>
  );
};
