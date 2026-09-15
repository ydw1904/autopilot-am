import { ReactNode } from "react";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

export interface Segment<T extends string> { value: T; label: ReactNode; title?: string; className?: string }

/** Pick one of a few options. The look comes from `className` (`view-switch`,
 *  `haul-tabs`, `segmented-control`); the chosen button gets `is-active`. */
export function SegmentedControl<T extends string>({ label, value, onChange, options, className }: {
  label: string;
  value: T;
  onChange: (value: T) => void;
  options: Segment<T>[];
  className: string;
}) {
  return (
    <div className={className} role="group" aria-label={label}>
      {options.map((option) => (
        <Button key={option.value} className={cn(option.className, value === option.value && "is-active")}
          title={option.title} aria-pressed={value === option.value} onClick={() => onChange(option.value)}>
          {option.label}
        </Button>
      ))}
    </div>
  );
}
