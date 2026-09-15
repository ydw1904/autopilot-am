import { ReactNode, useEffect, useState } from "react";
import { Filter, LucideIcon, Search, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";

/** A free-text filter: icon, input, and a clear button once there is text. */
export function SearchInput({ value, onChange, placeholder, icon: Icon = Search }: {
  value: string;
  onChange: (value: string) => void;
  placeholder: string;
  icon?: LucideIcon;
}) {
  return (
    <label className="search-control">
      <Icon size={17} />
      <Input value={value} onChange={(event) => onChange(event.target.value)} placeholder={placeholder} />
      {value && <Button onClick={() => onChange("")} aria-label="Clear search"><X size={15} /></Button>}
    </label>
  );
}

/** Every workspace's filter toolbar: the controls, then a row that counts the
 *  active filters, clears them, and says what is showing. */
export function FilterBar({ active, onClear, idleLabel = "No filters applied", status, className, children }: {
  active: number;
  onClear: () => void;
  /** What the count line says when nothing is filtered. */
  idleLabel?: string;
  /** Right-aligned summary, usually "Showing x of y". */
  status?: ReactNode;
  className?: string;
  children: ReactNode;
}) {
  return (
    <section className={`fleet-controls${className ? ` ${className}` : ""}`}>
      <div className="fleet-toolbar">{children}</div>
      <div className="active-filter-row">
        <span><Filter size={14} /> {active ? `${active} active filter${active === 1 ? "" : "s"}` : idleLabel}</span>
        {active > 0 && <Button onClick={onClear}>Clear all</Button>}
        {status}
      </div>
    </section>
  );
}

/** Search text trimmed and held until typing pauses, so a server-side filter
 *  does not refetch on every keystroke. Clearing applies at once. */
export function useDebouncedQuery(value: string): string {
  const text = value.trim();
  const [debounced, setDebounced] = useState(text);
  useEffect(() => {
    const timer = window.setTimeout(() => setDebounced(text), 220);
    return () => window.clearTimeout(timer);
  }, [text]);
  return text ? debounced : "";
}
