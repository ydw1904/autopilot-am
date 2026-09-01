import React, { useState } from "react";
import { Edit3, X } from "lucide-react";

interface BulkRenameModalProps {
  isOpen: boolean;
  onClose: () => void;
  selectedCount: number;
  onRename: (prefix: string, addNumbering: boolean) => Promise<void>;
}

export const BulkRenameModal: React.FC<BulkRenameModalProps> = ({
  isOpen,
  onClose,
  selectedCount,
  onRename,
}) => {
  const [prefix, setPrefix] = useState("");
  const [addNumbering, setAddNumbering] = useState(selectedCount > 1);
  const [loading, setLoading] = useState(false);

  if (!isOpen) return null;

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!prefix.trim()) return;
    setLoading(true);
    try {
      await onRename(prefix.trim(), addNumbering);
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
          <div className="w-10 h-10 rounded-lg bg-[#05164D]/10 border border-[#05164D]/20 flex items-center justify-center text-[#1D6FB8]">
            <Edit3 className="w-5 h-5" />
          </div>
          <div>
            <h3 className="text-base font-bold text-[#0A1E3C]">Bulk Rename Aircraft</h3>
            <p className="text-xs text-[#8B877C] font-mono">
              Apply to {selectedCount} selected aircraft
            </p>
          </div>
        </div>

        <form onSubmit={handleSubmit} className="space-y-4">
          <div>
            <label className="block text-xs font-mono font-medium text-[#4E4B43] mb-1.5 uppercase">
              New Prefix / Name
            </label>
            <input
              type="text"
              required
              placeholder="e.g. MPM-C001 or STORAGE"
              value={prefix}
              onChange={(e) => setPrefix(e.target.value)}
              className="w-full px-3.5 py-2.5 bg-white border border-[#E5E1D6] rounded-lg text-sm text-[#0A1E3C] placeholder-slate-400 focus:outline-none focus:border-[#05164D] font-mono"
            />
          </div>

          <label className="flex items-center gap-2 text-xs text-[#4E4B43] cursor-pointer">
            <input
              type="checkbox"
              checked={addNumbering}
              onChange={(e) => setAddNumbering(e.target.checked)}
              className="rounded bg-white border-[#CFC9BA] text-[#1D6FB8] focus:ring-0"
            />
            <span>Add sequential suffix (-001, -002, …)</span>
          </label>

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
              disabled={loading || !prefix.trim()}
              className="px-4 py-2 rounded-lg text-xs font-semibold bg-[#05164D] hover:bg-[#FFAD00] text-white transition disabled:opacity-50 flex items-center gap-1.5"
            >
              {loading ? "Renaming…" : "Apply Rename"}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
};
