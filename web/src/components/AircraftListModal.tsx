import React from "react";
import { X, Plane, ArrowRight } from "lucide-react";
import { LiveryPlane } from "../types";

interface AircraftListModalProps {
  isOpen: boolean;
  onClose: () => void;
  liveryName: string;
  skinId: number;
  planes: LiveryPlane[];
  onViewInFleet?: (skinId: number) => void;
}

export const AircraftListModal: React.FC<AircraftListModalProps> = ({
  isOpen,
  onClose,
  liveryName,
  skinId,
  planes,
  onViewInFleet,
}) => {
  if (!isOpen) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/75 backdrop-blur-sm">
      <div className="w-full max-w-lg bg-[#F8F6F1] border border-[#E5E1D6] rounded-xl shadow-2xl p-6 flex flex-col max-h-[85vh] relative">
        <button
          onClick={onClose}
          className="absolute top-4 right-4 text-[#8B877C] hover:text-[#0A1E3C]"
        >
          <X className="w-5 h-5" />
        </button>

        <div className="mb-4">
          <div className="flex items-center gap-2 mb-1">
            <span className="px-2 py-0.5 rounded bg-[#1E7E46]/10 border border-[#1E7E46]/20 text-[#1E7E46] text-[10px] font-mono font-bold">
              {planes.length} AIRCRAFT ASSIGNED
            </span>
          </div>
          <h3 className="text-base font-bold text-[#0A1E3C] leading-tight">{liveryName}</h3>
        </div>

        <div className="flex-1 overflow-y-auto pr-1 space-y-2 mb-4">
          {planes.map((p) => (
            <div
              key={p.aircraft_id}
              className="flex items-center justify-between p-3 rounded-lg bg-white border border-[#E5E1D6]/80 hover:border-[#CFC9BA] transition"
            >
              <div className="flex items-center gap-3">
                <div className="w-7 h-7 rounded-md bg-[#F1EEE6] flex items-center justify-center text-[#8B877C]">
                  <Plane className="w-4 h-4" />
                </div>
                <div>
                  <div className="text-xs font-mono font-bold text-[#0A1E3C]">{p.name}</div>
                  <div className="text-[10px] text-[#8B877C] font-mono">
                    {p.model} {p.hub && `· Hub ${p.hub}`}
                  </div>
                </div>
              </div>

              <div className="text-right">
                <span
                  className={`text-xs font-mono font-semibold ${
                    p.utilization >= 100
                      ? "text-[#1E7E46]"
                      : p.utilization > 0
                      ? "text-[#9E7600]"
                      : "text-[#8B877C]"
                  }`}
                >
                  {p.utilization.toFixed(0)}% util
                </span>
              </div>
            </div>
          ))}
        </div>

        <div className="flex items-center justify-between pt-3 border-t border-[#E5E1D6]">
          <button
            onClick={onClose}
            className="px-4 py-2 rounded-lg text-xs font-medium text-[#8B877C] hover:text-[#0A1E3C]"
          >
            Close
          </button>
          {onViewInFleet && (
            <button
              onClick={() => {
                onClose();
                onViewInFleet(skinId);
              }}
              className="px-4 py-2 rounded-lg text-xs font-semibold bg-[#05164D] hover:bg-[#FFAD00] text-white flex items-center gap-1.5 transition"
            >
              <span>View in Fleet Management</span>
              <ArrowRight className="w-3.5 h-3.5" />
            </button>
          )}
        </div>
      </div>
    </div>
  );
};
