import React, { useEffect, useState } from "react";
import { RefreshCw, Zap, Shield, Globe } from "lucide-react";

interface HeaderProps {
  title: string;
  subtitle: string;
  onSync?: () => void;
  syncing?: boolean;
}

export const Header: React.FC<HeaderProps> = ({ title, subtitle, onSync, syncing }) => {
  const [time, setTime] = useState("");

  useEffect(() => {
    const update = () => {
      const now = new Date();
      setTime(
        now.toLocaleTimeString("en-US", {
          hour12: false,
          hour: "2-digit",
          minute: "2-digit",
          second: "2-digit",
        })
      );
    };
    update();
    const interval = setInterval(update, 1000);
    return () => clearInterval(interval);
  }, []);

  return (
    <header className="h-16 border-b border-[#E5E1D6]/70 bg-white/70 backdrop-blur-md px-6 flex items-center justify-between flex-shrink-0 z-20">
      <div>
        <h1 className="text-base font-bold text-[#0A1E3C] tracking-tight flex items-center gap-2">
          {title}
        </h1>
        <p className="text-xs text-[#8B877C] font-mono tracking-tight">{subtitle}</p>
      </div>

      <div className="flex items-center gap-4">
        {onSync && (
          <button
            onClick={onSync}
            disabled={syncing}
            className={`inline-flex items-center gap-2 px-3.5 py-1.5 rounded-lg text-xs font-semibold tracking-wide transition-all shadow-sm ${
              syncing
                ? "bg-[#05164D]/20 text-[#1D6FB8] border border-[#05164D]/40 cursor-not-allowed"
                : "bg-[#05164D] hover:bg-[#0A2470] text-white shadow-sm ring-1 ring-[#05164D]/20"
            }`}
          >
            <RefreshCw className={`w-3.5 h-3.5 ${syncing ? "animate-spin" : ""}`} />
            <span>{syncing ? "Syncing Fleet…" : "Sync Fleet"}</span>
          </button>
        )}

        <div className="hidden md:flex items-center gap-2.5 px-3 py-1.5 rounded-lg bg-[#F8F6F1]/80 border border-[#E5E1D6] text-xs font-mono text-[#8B877C]">
          <Globe className="w-3.5 h-3.5 text-[#1D6FB8]" />
          <span>UTC {time}</span>
        </div>
      </div>
    </header>
  );
};
