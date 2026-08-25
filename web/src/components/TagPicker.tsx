import React, { useEffect, useMemo, useRef, useState } from "react";
import { Check, Plus, Tag } from "lucide-react";

// Starter vocabulary offered on an empty fleet so the first tag is a click
// rather than a typing exercise. These are only suggestions: nothing writes
// them anywhere until an operator actually applies one, and a tag that stops
// being useful simply drops off the "Your tags" list once no aircraft carries
// it.
export const PRESET_AIRCRAFT_TAGS = [
  "Needs reconfig",
  "Needs rename",
  "Unscheduled",
  "Reserve",
  "New delivery",
  "Livery swap",
  "For sale",
  "Retire soon",
  "Do not touch",
];

export interface TagSuggestion {
  tag: string;
  /** Aircraft currently carrying the tag; omitted for an unused preset. */
  count?: number;
}

interface TagPickerProps {
  /** Tags already in use across the fleet, with their live counts. */
  known: TagSuggestion[];
  /** Tags carried by every currently selected aircraft, shown ticked. */
  applied?: string[];
  onApply: (tag: string) => void | Promise<void>;
  busy?: boolean;
  placeholder?: string;
}

const MAX_TAG_LENGTH = 40;
const fold = (tag: string) => tag.trim().toLowerCase();

/** Combobox for the selection bar: pick an existing or suggested tag from the
 *  list, or type a new one. Unlike <MenuSelect> the value is free text, so this
 *  is an input with a filtered panel rather than a closed set of options. The
 *  panel opens upward because the selection bar sits at the bottom edge. */
export function TagPicker({ known, applied = [], onApply, busy, placeholder = "Add or pick a tag" }: TagPickerProps) {
  const [draft, setDraft] = useState("");
  const [open, setOpen] = useState(false);
  const [activeIndex, setActiveIndex] = useState(0);
  const rootRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  const appliedSet = useMemo(() => new Set(applied.map(fold)), [applied]);

  const groups = useMemo(() => {
    const needle = fold(draft);
    const matches = (tag: string) => !needle || tag.toLowerCase().includes(needle);
    const knownFolded = new Set(known.map((item) => fold(item.tag)));
    return [
      { label: "Your tags", options: known.filter((item) => matches(item.tag)) },
      {
        label: "Suggested",
        options: PRESET_AIRCRAFT_TAGS
          .filter((tag) => !knownFolded.has(fold(tag)) && matches(tag))
          .map((tag) => ({ tag }) as TagSuggestion),
      },
    ].filter((group) => group.options.length > 0);
  }, [draft, known]);

  const flat = useMemo(() => groups.flatMap((group) => group.options), [groups]);
  const trimmed = draft.trim();
  // A typed value that is not already an option gets its own "create" row, so
  // Enter never silently applies a near-match the operator did not choose.
  const isNew = Boolean(trimmed) && !flat.some((item) => fold(item.tag) === fold(trimmed));

  useEffect(() => setActiveIndex(0), [draft]);

  useEffect(() => {
    if (!open) return;
    const onPointerDown = (event: PointerEvent) => {
      if (!rootRef.current?.contains(event.target as Node)) setOpen(false);
    };
    document.addEventListener("pointerdown", onPointerDown);
    return () => document.removeEventListener("pointerdown", onPointerDown);
  }, [open]);

  const apply = async (tag: string) => {
    const cleaned = tag.trim();
    if (!cleaned || busy) return;
    setOpen(false);
    await onApply(cleaned);
    setDraft("");
    inputRef.current?.focus();
  };

  const rows = isNew ? [...flat, { tag: trimmed } as TagSuggestion] : flat;

  const onKeyDown = (event: React.KeyboardEvent) => {
    if (event.key === "Escape") {
      setOpen(false);
    } else if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      if (rows.length === 0) return;
      setOpen(true);
      const step = event.key === "ArrowDown" ? 1 : -1;
      setActiveIndex((index) => (index + step + rows.length) % rows.length);
    } else if (event.key === "Enter") {
      event.preventDefault();
      const picked = open && rows[activeIndex] ? rows[activeIndex].tag : trimmed;
      void apply(picked);
    }
  };

  const optionRow = (item: TagSuggestion, index: number, isCreate?: boolean) => (
    <button
      type="button"
      key={`${isCreate ? "new:" : "known:"}${item.tag}`}
      role="option"
      aria-selected={index === activeIndex}
      className={`tag-option${index === activeIndex ? " is-active" : ""}`}
      onMouseEnter={() => setActiveIndex(index)}
      onClick={() => void apply(item.tag)}
    >
      {isCreate ? <Plus size={14} /> : <Tag size={14} />}
      <span><b>{isCreate ? `Create “${item.tag}”` : item.tag}</b>{item.count !== undefined && <em>{item.count} aircraft</em>}</span>
      {appliedSet.has(fold(item.tag)) && <Check size={14} className="tag-option-check" />}
    </button>
  );

  return (
    <div className={`tag-picker${open ? " is-open" : ""}`} ref={rootRef}>
      <div className="tag-entry">
        <Tag size={14} />
        <input
          ref={inputRef}
          value={draft}
          role="combobox"
          aria-expanded={open}
          aria-autocomplete="list"
          aria-label="Custom tag to add"
          maxLength={MAX_TAG_LENGTH}
          placeholder={placeholder}
          onChange={(event) => { setDraft(event.target.value); setOpen(true); }}
          onFocus={() => setOpen(true)}
          onKeyDown={onKeyDown}
        />
        <button type="button" disabled={!trimmed || busy} onClick={() => void apply(trimmed)}>
          {busy ? "Adding" : "Add tag"}
        </button>
      </div>

      {open && rows.length > 0 && (
        <div className="tag-panel" role="listbox" aria-label="Tag suggestions">
          {groups.map((group) => (
            <div className="tag-group" key={group.label}>
              <p>{group.label}</p>
              {group.options.map((item) => optionRow(item, flat.indexOf(item)))}
            </div>
          ))}
          {isNew && <div className="tag-group">{optionRow({ tag: trimmed }, rows.length - 1, true)}</div>}
        </div>
      )}
    </div>
  );
}
