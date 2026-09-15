import { ReactNode } from "react";

/** Kicker, title, and whatever sits on the right: a count, controls, or both. */
export function SectionHeader({ kicker, title, count, children }: {
  kicker: ReactNode;
  title: ReactNode;
  count?: ReactNode;
  children?: ReactNode;
}) {
  return (
    <div className="section-title-row">
      <div><p className="section-kicker">{kicker}</p><h2>{title}</h2></div>
      {count != null && <span className="section-count">{count}</span>}
      {children}
    </div>
  );
}
