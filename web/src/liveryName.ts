import { LiveryItem } from "./types";

// Skin names read "<model> - <livery>" (e.g. "737-700 - South West Mexico"),
// but a few in-game names drop the space before the hyphen (e.g.
// "742- Air Force One"). Split on the first hyphen that's followed by
// whitespace so the model prefix (which never has a hyphen followed by
// whitespace itself, e.g. "747-200B") is stripped either way; anything
// without such a hyphen has no model prefix and sorts as a livery name on its own.
export function splitLiveryName(name: string): { model: string; livery: string } {
  const match = name.match(/^(.*?)-\s+(.*)$/);
  if (!match) return { model: "", livery: name };
  return { model: match[1].trim(), livery: match[2].trim() || name };
}

// Challenge liveries are named "<model> - Challenge <event>" (e.g.
// "777-200 - Challenge Xmas 2024"). The full name is kept intact everywhere;
// this returns just the event that follows the word "Challenge" ("Xmas 2024")
// so a card can tag which challenge a livery came from. Returns null for any
// livery that isn't a challenge skin.
export function challengeTag(name: string): string | null {
  const { livery } = splitLiveryName(name);
  const match = livery.match(/\bChallenge\b\s*(.*)$/i);
  const event = match?.[1]?.trim();
  return event ? event : null;
}

// The raw skin name's model prefix is sometimes an abbreviated type code
// (e.g. "742" instead of "747-200B") rather than a spacing quirk. Prefer the
// real model resolved from an owned aircraft (fleet.model, always accurate)
// and only fall back to the raw prefix when nothing is owned to resolve it from.
export function displayLiveryName(item: LiveryItem): string {
  const { model, livery } = splitLiveryName(item.name);
  const resolvedModel = item.aircraft[0]?.model || model;
  return resolvedModel ? `${resolvedModel} - ${livery}` : livery;
}
