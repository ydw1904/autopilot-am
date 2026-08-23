import React from "react";
import { Plane, Palette, Layers, Compass, BarChart3, Database, Sparkles, Terminal } from "lucide-react";

interface SidebarProps {
  activeTab: string;
  setActiveTab: (tab: string) => void;
  stats?: { total: number; special_skin_count: number };
}

export const Sidebar: React.FC<SidebarProps> = ({ activeTab, setActiveTab, stats }) => {
  const navItems = [
    {
      id: "fleet",
      label: "Fleet Management",
      icon: Plane,
      badge: stats ? `${stats.total.toLocaleString()}` : undefined,
    },
    {
      id: "liveries",
      label: "Livery Collection",
      icon: Palette,
      badge: stats ? `${stats.special_skin_count}` : undefined,
    },
    {
      id: "warehouse",
      label: "Warehouse / Idle",
      icon: Layers,
    },
    {
      id: "hubs",
      label: "Hubs & Routes",
      icon: Compass,
    },
    {
      id: "planner",
      label: "Revenue Planner",
      icon: BarChart3,
    },
  ];

  return (
    <aside className="w-64 bg-white/85 backdrop-blur-xl border-r border-[#E5E1D6]/70 flex flex-col h-screen select-none flex-shrink-0 z-30">
      {/* Brand Header */}
      <div className="p-5 border-b border-[#E5E1D6]/60 flex items-center justify-between">
        <div className="flex items-center gap-3">
          <div className="w-9 h-9 rounded-xl bg-[#05164D] flex items-center justify-center shadow-md shadow-[#05164D]/25 ring-1 ring-[#FFAD00]/40">
            <Plane className="w-5 h-5 text-[#FFAD00] transform -rotate-45" />
          </div>
          <div>
            <div className="flex items-center gap-1.5">
              <span className="font-bold text-sm text-[#0A1E3C] tracking-wider">AUTOPILOT</span>
              <span className="text-[10px] font-mono px-1.5 py-0.5 bg-[#05164D]/10 border border-[#05164D]/20 text-[#1D6FB8] rounded-md font-semibold">AM</span>
            </div>
            <p className="text-[10px] text-[#8B877C] font-mono tracking-tight">Fleet & Operations</p>
          </div>
        </div>
      </div>

      {/* Nav List */}
      <div className="flex-1 overflow-y-auto px-3 py-4 space-y-1">
        <div className="px-3 py-1.5 text-[10px] font-mono font-semibold text-[#8B877C] tracking-wider uppercase">
          Core Modules
        </div>
        {navItems.map((item) => {
          const Icon = item.icon;
          const isActive = activeTab === item.id;
          return (
            <button
              key={item.id}
              onClick={() => setActiveTab(item.id)}
              className={`w-full flex items-center justify-between px-3 py-2.5 rounded-lg text-xs font-medium transition-all duration-150 group ${
                isActive
                  ? "bg-[#FFAD00]/15 text-[#05164D] border border-[#FFAD00]/60 shadow-sm font-semibold"
                  : "text-[#8B877C] hover:text-[#0A1E3C] hover:bg-[#F1EEE6]/40"
              }`}
            >
              <div className="flex items-center gap-3">
                <Icon
                  className={`w-4 h-4 transition-colors ${
                    isActive ? "text-[#05164D]" : "text-[#8B877C] group-hover:text-[#4E4B43]"
                  }`}
                />
                <span>{item.label}</span>
              </div>
              {item.badge && (
                <span
                  className={`text-[10px] font-mono px-2 py-0.5 rounded-full ${
                    isActive
                      ? "bg-[#FFAD00]/25 text-[#05164D] border border-[#E8A800]/50"
                      : "bg-[#F1EEE6] text-[#8B877C] border border-[#CFC9BA]/50"
                  }`}
                >
                  {item.badge}
                </span>
              )}
            </button>
          );
        })}
      </div>

      {/* Footer System Status */}
      <div className="p-3 border-t border-[#E5E1D6]/60 bg-[#F5F2EC]/60">
        <div className="flex items-center justify-between text-[11px] font-mono text-[#8B877C]">
          <div className="flex items-center gap-2">
            <span className="w-2 h-2 rounded-full bg-[#1E7E46] shadow-[0_0_8px_#1E7E46] animate-pulse" />
            <span>CDP / Game Linked</span>
          </div>
          <span className="text-[10px] text-[#8B877C]">v2.1 Crane</span>
        </div>
      </div>
    </aside>
  );
};
