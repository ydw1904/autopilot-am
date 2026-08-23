import React, { useState } from "react";
import { Send, X } from "lucide-react";

interface AssignCircuitModalProps {
  isOpen: boolean;
  onClose: () => void;
  selectedCount: number;
  onAssign: (circuitCode: string) => Promise<void>;
}

export const AssignCircuitModal: React.FC<AssignCircuitModalProps> = ({
  isOpen,
  onClose,
  selectedCount,
  onAssign,
}) => {
  const [circuit, setCircuit] = useState("");
  const [loading, setLoading] = useState(false);

  if (!isOpen) return null;

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!circuit.trim()) return;
    setLoading(true);
    try {
      await onAssign(circuit.trim().toUpperCase());
      onClose();
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/70 backdrop-blur-sm">
      <div className="w-full max-w-md bg-[#F8F6F1] border border-[#E5E1D6] rounded-xl shadow-2xl p-6 relative">
        <button
          onClick={onClose}
          className="absolute top-4 right-4 text-[#8B877C] hover:text-[#0A1E3C]"
        >
          <X className="w-5 h-5" />
        </button>

        <div className="flex items-center gap-3 mb-4">
          <div className="w-10 h-10 rounded-lg bg-[#1E7E46]/10 border border-[#1E7E46]/20 flex items-center justify-center text-[#1E7E46]">
            <Send className="w-5 h-5" />
          </div>
          <div>
            <h3 className="text-base font-bold text-[#0A1E3C]">Assign to Circuit</h3>
            <p className="text-xs text-[#8B877C] font-mono">
              Rename {selectedCount} aircraft to canonical format
            </p>
          </div>
        </div>

        <form onSubmit={handleSubmit} className="space-y-4">
          <div>
            <label className="block text-xs font-mono font-medium text-[#4E4B43] mb-1.5 uppercase">
              Circuit Code
            </label>
            <input
              type="text"
              required
              placeholder="e.g. MPM-C001 or HKG-C005"
              value={circuit}
              onChange={(e) => setCircuit(e.target.value)}
              className="w-full px-3.5 py-2.5 bg-white border border-[#E5E1D6] rounded-lg text-sm text-[#0A1E3C] uppercase placeholder-slate-400 focus:outline-none focus:border-[#1E7E46] font-mono"
            />
            <p className="text-[11px] text-[#8B877C] font-mono mt-1">
              Planes will be sequentialized: {circuit ? `${circuit.toUpperCase()}-001, -002…` : "CODE-001, CODE-002…"}
            </p>
          </div>

          <div className="flex justify-end gap-2.5 pt-3 border-t border-[#E5E1D6]">
            <button
              type="button"
              onClick={onClose}
              className="px-4 py-2 rounded-lg text-xs font-medium text-[#8B877C] hover:text-[#0A1E3C] hover:bg-[#F1EEE6] transition"
            >
              Cancel
            </button>
            <button
              type="submit"
              disabled={loading || !circuit.trim()}
              className="px-4 py-2 rounded-lg text-xs font-semibold bg-[#1E7E46] hover:bg-[#1E7E46] text-white transition disabled:opacity-50 flex items-center gap-1.5"
            >
              {loading ? "Assigning…" : "Confirm Assignment"}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
};
