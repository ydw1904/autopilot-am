// The three liveries of the day are derived server-side from the date alone, so
// a reroll only lives as long as the mounted component: switching tabs or
// reloading asks again with no seed and lands back on the day's default pick.
// Parking the seed here, next to the day it belongs to, makes a reroll stick
// until midnight without giving the server any per-user state to keep.
const STORAGE_KEY = "crane.daily-livery-seed";

export interface DailyLiverySeed {
  /** The server's date when the reroll happened, as the API reports it. */
  day: string;
  seed: string;
}

export function readDailyLiverySeed(): DailyLiverySeed | null {
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as Partial<DailyLiverySeed> | null;
    if (typeof parsed?.day !== "string" || typeof parsed?.seed !== "string") return null;
    return { day: parsed.day, seed: parsed.seed };
  } catch {
    return null;
  }
}

export function writeDailyLiverySeed(entry: DailyLiverySeed): void {
  try {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(entry));
  } catch {
    /* a full or blocked store just means the reroll stops surviving a reload */
  }
}

export function clearDailyLiverySeed(): void {
  try {
    window.localStorage.removeItem(STORAGE_KEY);
  } catch {
    /* nothing to do -- the day check below drops the stale seed anyway */
  }
}
