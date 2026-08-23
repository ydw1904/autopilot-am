import React, { useState, useEffect } from "react";
import { Sidebar } from "./components/Sidebar";
import { Header } from "./components/Header";
import { FleetManagement } from "./components/FleetManagement";
import { LiveryCollection } from "./components/LiveryCollection";
import { FleetStats } from "./types";
import { fetchStats } from "./api";

export function App() {
  const [activeTab, setActiveTab] = useState<string>("fleet");
  const [stats, setStats] = useState<FleetStats | null>(null);
  const [filterSkinId, setFilterSkinId] = useState<number | null>(null);

  const loadStats = async () => {
    try {
      const data = await fetchStats();
      setStats(data);
    } catch (e) {
      console.error("Failed to fetch fleet stats", e);
    }
  };

  useEffect(() => {
    loadStats();
  }, []);

  const handleJumpToFleetWithSkin = (skinId: number) => {
    setFilterSkinId(skinId);
    setActiveTab("fleet");
  };

  const getHeaderInfo = () => {
    switch (activeTab) {
      case "fleet":
        return {
          title: "Fleet Operations Management",
          subtitle: "Live fleet overview, schedules, configurations & bulk management",
        };
      case "liveries":
        return {
          title: "Livery Collection & Aircraft Assignments",
          subtitle: "Special & event livery gallery · Excludes standard manufacturer liveries",
        };
      case "warehouse":
        return {
          title: "Aircraft Warehouse (Idle Fleet)",
          subtitle: "Manage 0% utilization planes across all hubs",
        };
      case "hubs":
        return {
          title: "Hubs & Network Routes",
          subtitle: "Route network connectivity, demand & fleet allocations",
        };
      case "planner":
        return {
          title: "Revenue Optimizer Planner",
          subtitle: "Circuit discovery, wave pricing & seat configuration search",
        };
      default:
        return {
          title: "Autopilot AM",
          subtitle: "Airlines Manager Operations Control",
        };
    }
  };

  const headerInfo = getHeaderInfo();

  return (
    <div className="flex h-screen w-screen overflow-hidden bg-[#F5F2EC] font-sans antialiased text-[#0A1E3C]">
      {/* Sidebar Navigation */}
      <Sidebar
        activeTab={activeTab}
        setActiveTab={(tab) => {
          if (tab !== "fleet") setFilterSkinId(null);
          setActiveTab(tab);
        }}
        stats={stats ? { total: stats.total, special_skin_count: stats.special_skin_count } : undefined}
      />

      {/* Main Workspace Area */}
      <div className="flex-1 flex flex-col h-screen overflow-hidden">
        <Header
          title={headerInfo.title}
          subtitle={headerInfo.subtitle}
        />

        <main className="flex-1 flex flex-col overflow-hidden">
          {activeTab === "fleet" && (
            <FleetManagement
              stats={stats}
              onRefreshStats={loadStats}
              initialSkinId={filterSkinId}
              onClearSkinFilter={() => setFilterSkinId(null)}
            />
          )}

          {activeTab === "liveries" && (
            <LiveryCollection onViewInFleet={handleJumpToFleetWithSkin} />
          )}

          {activeTab === "warehouse" && (
            <div className="p-8 flex flex-col items-center justify-center h-full text-[#8B877C] font-mono">
              <p className="text-sm">Warehouse filter view integrated into Fleet Management.</p>
              <button
                onClick={() => setActiveTab("fleet")}
                className="mt-3 px-4 py-2 rounded-lg bg-[#05164D] text-white font-bold text-xs"
              >
                Go to Fleet (Filter by Idle)
              </button>
            </div>
          )}

          {activeTab === "hubs" && (
            <div className="p-8 flex flex-col items-center justify-center h-full text-[#8B877C] font-mono">
              <p className="text-sm">Hub routes & network module.</p>
            </div>
          )}

          {activeTab === "planner" && (
            <div className="p-8 flex flex-col items-center justify-center h-full text-[#8B877C] font-mono">
              <p className="text-sm">Circuit optimization planner engine.</p>
            </div>
          )}
        </main>
      </div>
    </div>
  );
}

export default App;
