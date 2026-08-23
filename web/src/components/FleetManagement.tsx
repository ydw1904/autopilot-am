import React, { useState, useEffect, useMemo } from "react";
import {
  Plane,
  Layers,
  Activity,
  Sparkles,
  LayoutGrid,
  List,
  Search,
  CheckSquare,
  Square,
  Edit3,
  Send,
  X,
  Clock,
  Gauge,
  Tag,
  RefreshCw,
} from "lucide-react";
import { FleetAircraft, FleetStats } from "../types";
import { fetchFleet, triggerSyncFleet, bulkRenameAircraft, assignToCircuit } from "../api";
import { BulkRenameModal } from "./BulkRenameModal";
import { AssignCircuitModal } from "./AssignCircuitModal";

interface FleetManagementProps {
  stats?: FleetStats | null;
  onRefreshStats: () => void;
  initialSkinId?: number | null;
  onClearSkinFilter?: () => void;
}

export const FleetManagement: React.FC<FleetManagementProps> = ({
  stats,
  onRefreshStats,
  initialSkinId,
  onClearSkinFilter,
}) => {
  const [aircraft, setAircraft] = useState<FleetAircraft[]>([]);
  const [loading, setLoading] = useState(true);
  const [syncing, setSyncing] = useState(false);
  const [statusMsg, setStatusMsg] = useState<string | null>(null);

  // Filters
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
        sort_by: sortBy,
      });
      setAircraft(data);
    } catch (e: any) {
      setStatusMsg(`Failed to load fleet: ${e.message}`);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadData();
  }, [selectedHub, searchName, searchModel, utilFilter, skinFilter, activeSkinId, sortBy]);

  const handleSync = async () => {
    setSyncing(true);
    setStatusMsg("Connecting to game via CDP and syncing fleet…");
    try {
      const hubArg = selectedHub !== "ALL" ? selectedHub : undefined;
      const res = await triggerSyncFleet(hubArg);
      setStatusMsg(res.message);
      await loadData();
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
    if (onClearSkinFilter) onClearSkinFilter();
  };

  return (
    <div className="flex-1 flex flex-col overflow-hidden bg-[#F5F2EC]">
      {/* Top Action & KPI Bar */}
      <div className="p-6 pb-2 space-y-4 flex-shrink-0">
        {/* KPI Cards */}
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

        {/* Hub Filter Pills */}
        <div className="flex items-center gap-1.5 overflow-x-auto pb-1 scrollbar-none text-xs">
          <span className="text-[10px] font-mono font-bold text-[#8B877C] mr-1 uppercase">Hub:</span>
          <button
            onClick={() => setSelectedHub("ALL")}
            className={`px-3 py-1 rounded-full text-xs font-mono font-semibold transition ${
              selectedHub === "ALL"
                ? "bg-[#05164D]/20 text-[#1D6FB8] border border-[#05164D]/40 shadow-sm"
                : "bg-[#F8F6F1] border border-[#E5E1D6] text-[#8B877C] hover:text-[#0A1E3C]"
            }`}
          >
            ALL
          </button>
          {stats?.hubs.slice(0, 16).map((h) => (
            <button
              key={h.hub_iata}
              onClick={() => setSelectedHub(h.hub_iata)}
              className={`px-2.5 py-1 rounded-full text-xs font-mono transition flex items-center gap-1.5 ${
                selectedHub === h.hub_iata
                  ? "bg-[#05164D]/20 text-[#1D6FB8] border border-[#05164D]/40 font-bold"
                  : "bg-[#F8F6F1] border border-[#E5E1D6] text-[#8B877C] hover:text-[#0A1E3C]"
              }`}
            >
              <span>{h.hub_iata}</span>
              <span className="text-[10px] opacity-70">({h.count})</span>
            </button>
          ))}
        </div>

        {/* Filter Toolbar */}
        <div className="bg-[#FFFFFF]/60 backdrop-blur border border-[#E5E1D6]/80 rounded-xl p-3 flex flex-wrap gap-2.5 items-center justify-between">
          <div className="flex flex-wrap items-center gap-2 flex-1 min-w-[280px]">
            {/* Search Name */}
            <div className="relative min-w-[180px] flex-1 max-w-[260px]">
              <Search className="w-3.5 h-3.5 text-[#8B877C] absolute left-3 top-1/2 -translate-y-1/2" />
              <input
                type="text"
                placeholder="Search plane or circuit…"
                value={searchName}
                onChange={(e) => setSearchName(e.target.value)}
                className="w-full pl-8 pr-3 py-1.5 bg-white/80 border border-[#E5E1D6] rounded-lg text-xs text-[#0A1E3C] placeholder-slate-400 font-mono focus:outline-none focus:border-[#05164D]"
              />
            </div>

            {/* Search Model */}
            <input
              type="text"
              placeholder="Filter model (e.g. 747)…"
              value={searchModel}
              onChange={(e) => setSearchModel(e.target.value)}
              className="w-36 px-3 py-1.5 bg-white/80 border border-[#E5E1D6] rounded-lg text-xs text-[#0A1E3C] placeholder-slate-400 font-mono focus:outline-none focus:border-[#05164D]"
            />

            {/* Utilization */}
            <select
              value={utilFilter}
              onChange={(e) => setUtilFilter(e.target.value)}
              className="px-3 py-1.5 bg-white/80 border border-[#E5E1D6] rounded-lg text-xs text-[#0A1E3C] font-mono focus:outline-none focus:border-[#05164D]"
            >
              <option value="all">All Utilization</option>
              <option value="active">Active (&gt;0%)</option>
              <option value="idle">Idle Only (0%)</option>
              <option value="full">Full (100%)</option>
              <option value="partial">Partial (&lt;100%)</option>
            </select>

            {/* Livery Filter */}
            <select
              value={skinFilter}
              onChange={(e) => setSkinFilter(e.target.value as any)}
              className="px-3 py-1.5 bg-white/80 border border-[#E5E1D6] rounded-lg text-xs text-[#0A1E3C] font-mono focus:outline-none focus:border-[#05164D]"
            >
              <option value="all">All Liveries</option>
              <option value="special">Special Liveries Only</option>
              <option value="manufacturer">Manufacturer Only</option>
            </select>

            {/* Active Skin Banner */}
            {activeSkinId && (
              <div className="flex items-center gap-1.5 px-2.5 py-1 rounded-lg bg-[#05164D]/10 border border-[#05164D]/30 text-[#1D6FB8] text-xs font-mono">
                <span>Skin #{activeSkinId}</span>
                <button onClick={() => setActiveSkinId(null)} className="hover:text-[#0A1E3C]">
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

          {/* View toggle & Actions */}
          <div className="flex items-center gap-2">
            <div className="flex items-center bg-white border border-[#E5E1D6] rounded-lg p-0.5">
              <button
                onClick={() => setViewMode("grid")}
                className={`p-1.5 rounded-md text-xs transition ${
                  viewMode === "grid" ? "bg-[#05164D]/20 text-[#1D6FB8]" : "text-[#8B877C] hover:text-[#0A1E3C]"
                }`}
                title="Grid view"
              >
                <LayoutGrid className="w-4 h-4" />
              </button>
              <button
                onClick={() => setViewMode("table")}
                className={`p-1.5 rounded-md text-xs transition ${
                  viewMode === "table" ? "bg-[#05164D]/20 text-[#1D6FB8]" : "text-[#8B877C] hover:text-[#0A1E3C]"
                }`}
                title="Table view"
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

        {/* Selection Bar */}
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
                  {selectedIds.size > 0 ? `${selectedIds.size} of ${aircraft.length} selected` : "Select All"}
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

            <div>
              <span>Showing {aircraft.length.toLocaleString()} aircraft</span>
            </div>
          </div>
        )}
      </div>

      {/* Main Content Area */}
      <div className="flex-1 overflow-y-auto px-6 py-2">
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
              const isSpecial = ac.skin_name && !ac.skin_name.includes("Manufacturer");
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
                  {/* Card Header: Checkbox + Name + Hub */}
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
                    <span className="px-2 py-0.5 rounded bg-[#05164D]/10 border border-[#05164D]/20 text-[#1D6FB8] text-[10px] font-mono font-bold flex-shrink-0">
                      {ac.hub_iata}
                    </span>
                  </div>

                  {/* Artwork & Specs */}
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
                        {ac.range_km ? ` · ${ac.range_km.toLocaleString()}km` : ""}
                      </p>
                      {ac.seats_eco !== undefined && ac.seats_eco !== null && (
                        <p className="text-[10px] font-mono text-[#8B877C] mt-0.5">
                          {ac.seats_eco}E / {ac.seats_bus || 0}B / {ac.seats_first || 0}F
                        </p>
                      )}
                    </div>
                  </div>

                  {/* Utilization Meter */}
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

                  {/* Footer tags */}
                  <div className="flex items-center justify-between pt-2 border-t border-[#E5E1D6]/50 text-[10px] font-mono">
                    {isSpecial ? (
                      <span className="px-1.5 py-0.5 rounded bg-[#7C5CBF]/15 border border-[#7C5CBF]/30 text-[#7C5CBF] font-semibold truncate max-w-[150px]">
                        ★ {ac.skin_name?.split(" - ").pop()}
                      </span>
                    ) : (
                      <span className="text-[#8B877C]">Mfg Livery</span>
                    )}

                    {typeof ac.wear === "number" && (
                      <span className="text-[#8B877C]">Wear {ac.wear.toFixed(1)}%</span>
                    )}
                  </div>
                </div>
              );
            })}
          </div>
        ) : (
          /* Table View */
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
                    <th className="p-3">Utilization</th>
                    <th className="p-3">Seating</th>
                    <th className="p-3">Livery</th>
                    <th className="p-3">Wear</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-800/60">
                  {aircraft.map((ac) => {
                    const isSelected = selectedIds.has(ac.aircraft_id);
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
                        <td className="p-3 font-bold text-[#0A1E3C] flex items-center gap-2.5">
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
                        </td>
                        <td className="p-3 text-[#4E4B43]">{ac.model}</td>
                        <td className="p-3">
                          <span className="px-2 py-0.5 rounded bg-[#05164D]/10 border border-[#05164D]/20 text-[#1D6FB8] font-bold">
                            {ac.hub_iata}
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
                          {ac.seats_eco !== null
                            ? `${ac.seats_eco}E / ${ac.seats_bus || 0}B / ${ac.seats_first || 0}F`
                            : "Standard"}
                        </td>
                        <td className="p-3">
                          {ac.skin_name && !ac.skin_name.includes("Manufacturer") ? (
                            <span className="px-2 py-0.5 rounded bg-[#7C5CBF]/15 border border-[#7C5CBF]/30 text-[#7C5CBF]">
                              {ac.skin_name.split(" - ").pop()}
                            </span>
                          ) : (
                            <span className="text-[#8B877C]">Manufacturer</span>
                          )}
                        </td>
                        <td className="p-3 text-[#8B877C]">
                          {typeof ac.wear === "number" ? `${ac.wear.toFixed(1)}%` : "—"}
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
