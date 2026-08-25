import React, { useEffect, useMemo, useRef, useState } from "react";
import { Check, ChevronDown } from "lucide-react";

export type MenuIcon = React.ComponentType<{ size?: number }>;

export interface MenuOption {
  value: string;
  label: string;
  /** Short qualifier shown next to the label, e.g. a direction or a count. */
  hint?: string;
  icon?: MenuIcon;
}

export interface MenuGroup {
  label?: string;
  options: MenuOption[];
}

interface MenuSelectProps {
  /** Caption printed above the current value, e.g. "Hub" or "Sort by". */
  label: string;
  value: string;
  onChange: (value: string) => void;
  /** Flat list, or `groups` for a list broken up by headings. Pass one. */
  options?: MenuOption[];
  groups?: MenuGroup[];
  /** Trigger icon used when the selected option carries none of its own. */
  icon?: MenuIcon;
  /** Side of the trigger the panel lines up with. Default "left". */
  align?: "left" | "right";
  className?: string;
}

/** The app's one dropdown. Every filter, sort, and picker uses this instead of
 *  a native `<select>`: the OS popup is tiny, unstyleable, and looks foreign on
 *  a dark UI. See AGENTS.md ("Dropdowns") before adding another one. */
export function MenuSelect({ label, value, onChange, options, groups, icon: FallbackIcon, align = "left", className }: MenuSelectProps) {
  const [open, setOpen] = useState(false);
  const [activeIndex, setActiveIndex] = useState(0);
  const rootRef = useRef<HTMLDivElement>(null);
  const panelRef = useRef<HTMLDivElement>(null);

  const resolvedGroups = useMemo<MenuGroup[]>(() => groups ?? [{ options: options ?? [] }], [groups, options]);
  const flat = useMemo(() => resolvedGroups.flatMap((group) => group.options), [resolvedGroups]);
  const current = flat.find((option) => option.value === value) || flat[0];

  const focusTrigger = () => rootRef.current?.querySelector<HTMLButtonElement>(".menu-select-trigger")?.focus();

  useEffect(() => {
    if (!open) return;
    setActiveIndex(Math.max(0, flat.findIndex((option) => option.value === value)));
    const onPointerDown = (event: PointerEvent) => {
      if (!rootRef.current?.contains(event.target as Node)) setOpen(false);
    };
    document.addEventListener("pointerdown", onPointerDown);
    return () => document.removeEventListener("pointerdown", onPointerDown);
  }, [open, flat, value]);

  useEffect(() => {
    if (!open) return;
    panelRef.current?.focus();
    panelRef.current?.querySelector(".menu-select-option.is-selected")?.scrollIntoView({ block: "nearest" });
  }, [open]);

  const commit = (next: string) => {
    onChange(next);
    setOpen(false);
    focusTrigger();
  };

  const onKeyDown = (event: React.KeyboardEvent) => {
    if (event.key === "Escape") {
      setOpen(false);
      focusTrigger();
    } else if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      const step = event.key === "ArrowDown" ? 1 : -1;
      setActiveIndex((index) => (index + step + flat.length) % flat.length);
    } else if (event.key === "Home" || event.key === "End") {
      event.preventDefault();
      setActiveIndex(event.key === "Home" ? 0 : flat.length - 1);
    } else if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      if (flat[activeIndex]) commit(flat[activeIndex].value);
    } else if (event.key === "Tab") {
      setOpen(false);
    }
  };

  if (!current) return null;
  const TriggerIcon = current.icon || FallbackIcon;

  return (
    <div className={`menu-select${open ? " is-open" : ""}${className ? ` ${className}` : ""}`} ref={rootRef}>
      <button
        type="button"
        className="menu-select-trigger"
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-label={`${label}: ${current.label}`}
        onClick={() => setOpen((wasOpen) => !wasOpen)}
        onKeyDown={(event) => {
          if (event.key === "ArrowDown" && !open) {
            event.preventDefault();
            setOpen(true);
          }
        }}
      >
        <span className="menu-select-caption">{label}</span>
        <span className="menu-select-current">
          {TriggerIcon && <TriggerIcon size={15} />}
          <b>{current.label}</b>
          {current.hint && <em>{current.hint}</em>}
        </span>
        <ChevronDown size={16} className="menu-select-chevron" />
      </button>

      {open && (
        <div
          className={`menu-select-panel${align === "right" ? " is-right" : ""}`}
          role="listbox"
          aria-label={label}
          tabIndex={-1}
          ref={panelRef}
          onKeyDown={onKeyDown}
        >
          {resolvedGroups.map((group, groupIndex) => (
            <div className="menu-select-group" key={group.label || groupIndex}>
              {group.label && <p>{group.label}</p>}
              {group.options.map((option) => {
                const Icon = option.icon;
                const index = flat.indexOf(option);
                return (
                  <button
                    type="button"
                    key={option.value}
                    role="option"
                    aria-selected={option.value === value}
                    className={`menu-select-option${option.value === value ? " is-selected" : ""}${index === activeIndex ? " is-active" : ""}`}
                    onMouseEnter={() => setActiveIndex(index)}
                    onClick={() => commit(option.value)}
                  >
                    {Icon && <Icon size={16} />}
                    <span>
                      <b>{option.label}</b>
                      {option.hint && <em>{option.hint}</em>}
                    </span>
                    {option.value === value && <Check size={16} className="menu-select-check" />}
                  </button>
                );
              })}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
