import React, { useState, useEffect, useMemo } from "react";
import {
  Palette,
  Search,
  CheckCircle2,
  XCircle,
  Plane,
  ArrowRight,
  RefreshCw,
  Gift,
  Award,
} from "lucide-react";
import { LiveryItem, LiveryPlane } from "../types";
import { fetchLiveries } from "../api";
import { AircraftListModal } from "./AircraftListModal";

interface LiveryCollectionProps {
  onViewInFleet: (skinId: number) => void;
}

export const LiveryCollection: React.FC<LiveryCollectionProps> = ({ onViewInFleet }) => {
  const [liveries, setLiveries] = useState<LiveryItem[]>([]);
  const [loading, setLoading] = useState(true);

  // Filters
  const [statusFilter, setStatusFilter] = useState<"all" | "owned" | "unowned">("all");
  const [searchQuery, setSearchQuery] = useState("");
  const [modelQuery, setModelQuery] = useState("");
  const [sortBy, setSortBy] = useState<"owned_desc" | "owned_asc" | "name">("owned_desc");

  // Modal for aircraft list
  const [modalData, setModalData] = useState<{
    isOpen: boolean;
    name: string;
    skinId: number;
    planes: LiveryPlane[];
  }>({
    isOpen: false,
    name: "",
    skinId: 0,
    planes: [],
  });

  const loadData = async () => {
    setLoading(true);
    try {
      const data = await fetchLiveries({
        status_filter: statusFilter,
        model_query: modelQuery.trim() || undefined,
        search_query: searchQuery.trim() || undefined,
      });

      const sorted = [...data];
      if (sortBy === "owned_desc") {
        sorted.sort((a, b) => b.owned_count - a.owned_count || a.name.localeCompare(b.name));
      } else if (sortBy === "owned_asc") {
        sorted.sort((a, b) => a.owned_count - b.owned_count || a.name.localeCompare(b.name));
      } else {
        sorted.sort((a, b) => a.name.localeCompare(b.name));
      }

      setLiveries(sorted);
    } catch (err) {
      console.error("Failed to load liveries", err);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadData();
  }, [statusFilter, searchQuery, modelQuery, sortBy]);

  // Overall collection stats
  const totalCount = liveries.length;
  const ownedCount = useMemo(() => liveries.filter((l) => l.is_owned).length, [liveries]);
  const unownedCount = totalCount - ownedCount;
  const completionPct = totalCount > 0 ? Math.round((ownedCount / totalCount) * 100) : 0;

  const handleOpenAircraftModal = (item: LiveryItem) => {
    setModalData({
      isOpen: true,
      name: item.name,
      skinId: item.skin_id,
      planes: item.aircraft,
    });
  };

  return (
    <div className="flex-1 flex flex-col overflow-hidden bg-[#F5F2EC]">
      {/* Top Header & Metrics */}
      <div className="p-6 pb-2 space-y-4 flex-shrink-0">
        {/* KPI Cards & Completion Bar */}
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
          <div className="bg-[#FFFFFF]/80 backdrop-blur border border-[#E5E1D6]/80 rounded-xl p-3.5 flex flex-col justify-between">
            <span className="text-[10px] font-mono font-semibold text-[#8B877C] uppercase tracking-wider">
              Special Liveries
            </span>
            <div className="flex items-baseline gap-1 mt-1">
              <span className="text-2xl font-black text-[#0A1E3C] font-mono">{totalCount}</span>
              <span className="text-[10px] text-[#8B877C] font-mono">in pool</span>
            </div>
          </div>

          <div className="bg-[#FFFFFF]/80 backdrop-blur border border-[#E5E1D6]/80 rounded-xl p-3.5 flex flex-col justify-between hover:border-[#1E7E46]/30 transition">
            <span className="text-[10px] font-mono font-semibold text-[#1E7E46] uppercase tracking-wider flex items-center gap-1.5">
              <CheckCircle2 className="w-3.5 h-3.5" />
              Collected / Owned
            </span>
            <div className="flex items-baseline gap-1 mt-1">
              <span className="text-2xl font-black text-[#1E7E46] font-mono">{ownedCount}</span>
              <span className="text-[10px] text-[#8B877C] font-mono">active</span>
            </div>
          </div>

          <div className="bg-[#FFFFFF]/80 backdrop-blur border border-[#E5E1D6]/80 rounded-xl p-3.5 flex flex-col justify-between hover:border-[#E8A800]/30 transition">
            <span className="text-[10px] font-mono font-semibold text-[#9E7600] uppercase tracking-wider flex items-center gap-1.5">
              <XCircle className="w-3.5 h-3.5" />
              Missing / Catalog
            </span>
            <div className="flex items-baseline gap-1 mt-1">
              <span className="text-2xl font-black text-[#9E7600] font-mono">{unownedCount}</span>
              <span className="text-[10px] text-[#8B877C] font-mono">to unlock</span>
            </div>
          </div>

          <div className="bg-[#FFFFFF]/80 backdrop-blur border border-[#E5E1D6]/80 rounded-xl p-3.5 flex flex-col justify-between hover:border-[#05164D]/30 transition">
            <span className="text-[10px] font-mono font-semibold text-[#1D6FB8] uppercase tracking-wider">
              Collection Rate
            </span>
            <div className="flex items-baseline gap-1 mt-1">
              <span className="text-2xl font-black text-[#1D6FB8] font-mono">{completionPct}%</span>
              <span className="text-[10px] text-[#8B877C] font-mono">completed</span>
            </div>
          </div>
        </div>

        {/* Progress Bar */}
        <div className="bg-[#FFFFFF]/60 border border-[#E5E1D6]/80 rounded-xl p-3">
          <div className="flex justify-between items-center text-xs font-mono mb-2">
            <span className="text-[#4E4B43] font-semibold flex items-center gap-1.5">
              <Award className="w-3.5 h-3.5 text-[#1D6FB8]" />
              Special Livery Album Progress
            </span>
            <span className="text-[#1D6FB8] font-bold">{ownedCount} of {totalCount} collected ({completionPct}%)</span>
          </div>
          <div className="w-full h-2 bg-white rounded-full overflow-hidden border border-[#E5E1D6]">
            <div
              className="h-full bg-gradient-to-r from-[#05164D] via-[#1E7E46] to-[#FFAD00] rounded-full transition-all duration-500"
              style={{ width: `${completionPct}%` }}
            />
          </div>
        </div>

        {/* Filter Toolbar */}
        <div className="bg-[#FFFFFF]/60 backdrop-blur border border-[#E5E1D6]/80 rounded-xl p-3 space-y-3">
          {/* Row 1: Status Pills */}
          <div className="flex flex-wrap items-center gap-3 justify-between">
            {/* Status */}
            <div className="flex items-center gap-1.5">
              <span className="text-[10px] font-mono font-bold text-[#8B877C] uppercase mr-1">Status:</span>
              <button
                onClick={() => setStatusFilter("all")}
                className={`px-3 py-1 rounded-full text-xs font-mono transition ${
                  statusFilter === "all"
                    ? "bg-[#05164D]/20 text-[#1D6FB8] border border-[#05164D]/40 font-bold"
                    : "bg-[#F8F6F1] border border-[#E5E1D6] text-[#8B877C] hover:text-[#0A1E3C]"
                }`}
              >
                All ({totalCount})
              </button>
              <button
                onClick={() => setStatusFilter("owned")}
                className={`px-3 py-1 rounded-full text-xs font-mono transition flex items-center gap-1 ${
                  statusFilter === "owned"
                    ? "bg-[#1E7E46]/20 text-[#1E7E46] border border-[#1E7E46]/40 font-bold"
                    : "bg-[#F8F6F1] border border-[#E5E1D6] text-[#8B877C] hover:text-[#1E7E46]"
                }`}
              >
                <CheckCircle2 className="w-3 h-3" /> Owned Only
              </button>
              <button
                onClick={() => setStatusFilter("unowned")}
                className={`px-3 py-1 rounded-full text-xs font-mono transition flex items-center gap-1 ${
                  statusFilter === "unowned"
                    ? "bg-[#FFAD00]/20 text-[#9E7600] border border-[#E8A800]/40 font-bold"
                    : "bg-[#F8F6F1] border border-[#E5E1D6] text-[#8B877C] hover:text-[#9E7600]"
                }`}
              >
                <XCircle className="w-3 h-3" /> Missing / Unowned
              </button>
            </div>
          </div>

          {/* Row 2: Search and Sort */}
          <div className="flex flex-wrap items-center gap-2.5 pt-2 border-t border-[#E5E1D6]/60">
            <div className="relative flex-1 min-w-[200px] max-w-sm">
              <Search className="w-3.5 h-3.5 text-[#8B877C] absolute left-3 top-1/2 -translate-y-1/2" />
              <input
                type="text"
                placeholder="Search livery or booster event…"
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                className="w-full pl-8 pr-3 py-1.5 bg-white/80 border border-[#E5E1D6] rounded-lg text-xs text-[#0A1E3C] placeholder-slate-400 font-mono focus:outline-none focus:border-[#05164D]"
              />
            </div>

            <input
              type="text"
              placeholder="Model (e.g. 737, A380)…"
              value={modelQuery}
              onChange={(e) => setModelQuery(e.target.value)}
              className="w-36 px-3 py-1.5 bg-white/80 border border-[#E5E1D6] rounded-lg text-xs text-[#0A1E3C] placeholder-slate-400 font-mono focus:outline-none focus:border-[#05164D]"
            />

            <select
              value={sortBy}
              onChange={(e) => setSortBy(e.target.value as any)}
              className="px-3 py-1.5 bg-white/80 border border-[#E5E1D6] rounded-lg text-xs text-[#0A1E3C] font-mono focus:outline-none focus:border-[#05164D]"
            >
              <option value="owned_desc">Sort: Most Owned Planes</option>
              <option value="owned_asc">Sort: Least Owned Planes</option>
              <option value="name">Sort: Name (A-Z)</option>
            </select>

            <button
              onClick={() => {
                setStatusFilter("all");
                setSearchQuery("");
                setModelQuery("");
              }}
              className="px-2.5 py-1.5 text-xs text-[#8B877C] hover:text-[#0A1E3C] font-mono hover:bg-[#F1EEE6] rounded-lg transition ml-auto"
            >
              Reset
            </button>
          </div>
        </div>
      </div>

      {/* Livery Gallery Grid */}
      <div className="flex-1 overflow-y-auto px-6 py-2">
        {loading ? (
          <div className="h-64 flex flex-col items-center justify-center gap-3 text-[#8B877C] font-mono">
            <RefreshCw className="w-6 h-6 animate-spin text-[#1D6FB8]" />
            <span>Loading special liveries…</span>
          </div>
        ) : liveries.length === 0 ? (
          <div className="h-64 flex flex-col items-center justify-center gap-2 text-[#8B877C] font-mono">
            <Palette className="w-8 h-8 opacity-40 text-[#B6B1A4]" />
            <p className="text-sm">No special liveries match current filters.</p>
          </div>
        ) : (
          <div className="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-3 xl:grid-cols-4 gap-4 pb-12">
            {liveries.map((item) => {
              return (
                <div
                  key={item.skin_id}
                  className="bg-[#FFFFFF]/90 backdrop-blur rounded-xl border border-[#E5E1D6]/80 p-4 flex flex-col justify-between gap-3.5 transition-all duration-200 hover:-translate-y-1 hover:shadow-xl"
                >
                  {/* Image Box */}
                  <div className="relative w-full h-32 rounded-lg bg-white border border-[#E5E1D6]/80 flex items-center justify-center overflow-hidden p-2">
                    <img
                      src={`/api/skin_image/${item.skin_id}`}
                      alt={item.name}
                      className="max-w-full max-h-full object-contain filter drop-shadow-md transition-transform duration-200 group-hover:scale-105"
                      loading="lazy"
                    />

                    {/* Top Right Ownership Badge */}
                    <div className="absolute top-2 right-2">
                      {item.is_owned ? (
                        <span className="px-2 py-0.5 rounded-full text-[10px] font-mono font-bold bg-[#1E7E46]/20 border border-[#1E7E46]/40 text-[#1E7E46] flex items-center gap-1 shadow-sm">
                          <CheckCircle2 className="w-3 h-3" /> OWNED ({item.owned_count})
                        </span>
                      ) : (
                        <span className="px-2 py-0.5 rounded-full text-[10px] font-mono font-semibold bg-[#F8F6F1]/80 border border-[#CFC9BA] text-[#8B877C] flex items-center gap-1">
                          <XCircle className="w-3 h-3" /> NOT OWNED
                        </span>
                      )}
                    </div>
                  </div>

                  {/* Title & Info */}
                  <div>
                    <h4 className="text-sm font-bold text-[#0A1E3C] leading-snug line-clamp-2">
                      {item.name}
                    </h4>
                    {item.boosters && (
                      <p className="text-[10px] font-mono text-[#8B877C] mt-1 flex items-center gap-1 truncate">
                        <Gift className="w-3 h-3 text-[#1D6FB8]" />
                        <span>{item.boosters}</span>
                      </p>
                    )}
                  </div>

                  {/* Assigned Aircraft Section */}
                  <div className="bg-white/80 border border-[#E5E1D6]/70 rounded-lg p-2.5 space-y-1.5 mt-auto">
                    <div className="flex justify-between items-center text-[10px] font-mono">
                      <span className="text-[#8B877C] font-bold uppercase">Aircraft in Fleet</span>
                      <span
                        className={`font-semibold ${
                          item.is_owned ? "text-[#1E7E46]" : "text-[#8B877C]"
                        }`}
                      >
                        {item.owned_count} active
                      </span>
                    </div>

                    {item.is_owned && item.aircraft.length > 0 ? (
                      <div className="flex flex-wrap gap-1.5 items-center">
                        {item.aircraft.slice(0, 4).map((p) => (
                          <span
                            key={p.aircraft_id}
                            className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded bg-[#F8F6F1] border border-[#E5E1D6] text-[10px] font-mono text-[#0A1E3C]"
                          >
                            <Plane className="w-2.5 h-2.5 text-[#1D6FB8]" />
                            <span className="truncate max-w-[90px]">{p.name}</span>
                          </span>
                        ))}
                        {item.aircraft.length > 4 && (
                          <button
                            onClick={() => handleOpenAircraftModal(item)}
                            className="text-[10px] font-mono text-[#1D6FB8] hover:text-[#1D6FB8] underline font-medium"
                          >
                            +{item.aircraft.length - 4} more…
                          </button>
                        )}
                      </div>
                    ) : (
                      <p className="text-[10px] text-[#8B877C] italic">
                        Not currently flying on any aircraft.
                      </p>
                    )}
                  </div>

                  {/* Card Action */}
                  <div className="flex items-center justify-between pt-1">
                    {item.is_owned ? (
                      <button
                        onClick={() => onViewInFleet(item.skin_id)}
                        className="w-full flex items-center justify-center gap-1.5 py-1.5 px-3 rounded-lg bg-[#05164D]/10 hover:bg-[#05164D]/20 border border-[#05164D]/30 text-[#1D6FB8] text-xs font-mono font-semibold transition"
                      >
                        <span>View in Fleet</span>
                        <ArrowRight className="w-3.5 h-3.5" />
                      </button>
                    ) : (
                      <div className="w-full py-1.5 text-center text-[10px] font-mono text-[#8B877C]">
                        Available via drops / auction
                      </div>
                    )}
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </div>

      {/* Aircraft List Modal */}
      <AircraftListModal
        isOpen={modalData.isOpen}
        onClose={() => setModalData((prev) => ({ ...prev, isOpen: false }))}
        liveryName={modalData.name}
        skinId={modalData.skinId}
        planes={modalData.planes}
        onViewInFleet={onViewInFleet}
      />
    </div>
  );
};
