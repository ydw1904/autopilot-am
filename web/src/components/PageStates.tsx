import { AlertTriangle, LucideIcon, SlidersHorizontal } from "lucide-react";

/** A read failed. `inline` sits inside a section instead of replacing the page. */
export function ErrorState({ title, message, inline = false }: { title: string; message: string; inline?: boolean }) {
  return (
    <div className={`fatal-state${inline ? " is-inline" : ""}`} role="alert">
      <AlertTriangle size={inline ? 20 : 22} />
      <div><strong>{title}</strong><p>{message}</p></div>
    </div>
  );
}

/** Placeholder blocks while a workspace's first read is in flight. */
export function LoadingState() {
  return <div className="command-skeleton" aria-busy="true"><div className="skeleton-block is-wide" /><div className="skeleton-block is-tall" /></div>;
}

/** Nothing to show. `positive` is the all-clear ("nothing in delivery"), not a
 *  filter that matched nothing. */
export function EmptyState({ title, hint, icon: Icon = SlidersHorizontal, positive = false }: {
  title: string;
  hint?: string;
  icon?: LucideIcon;
  positive?: boolean;
}) {
  return (
    <div className={`empty-state${positive ? " is-positive" : ""}`}>
      <Icon size={20} />
      <strong>{title}</strong>
      {hint && <span>{hint}</span>}
    </div>
  );
}
