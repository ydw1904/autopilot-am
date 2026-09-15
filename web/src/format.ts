// One set of number and date formatters, so a figure reads the same in every
// workspace.

export const EMPTY = "—";

export const integer = new Intl.NumberFormat(undefined, { maximumFractionDigits: 0 });
export const wholeMoney = new Intl.NumberFormat(undefined, { style: "currency", currency: "USD", maximumFractionDigits: 0 });
export const compactMoney = new Intl.NumberFormat(undefined, {
  style: "currency",
  currency: "USD",
  notation: "compact",
  maximumFractionDigits: 1,
});

/** `$2.00B` / `$850M` / `$1,250`: the market price style `parseMoney` reads back. */
export function shortMoney(value: number | null | undefined): string {
  if (value == null) return EMPTY;
  if (value >= 1_000_000_000) return `$${(value / 1_000_000_000).toFixed(2)}B`;
  if (value >= 1_000_000) return `$${(value / 1_000_000).toFixed(0)}M`;
  return `$${integer.format(value)}`;
}

/** Game and SQLite timestamps (`2024-11-03 23:01:21.000000`) are UTC with no
 *  zone marker, which browsers would read as local time, and carry six
 *  fractional digits where Date accepts three. ISO strings pass through. */
export function parseGameDate(value: string | null | undefined): Date | null {
  if (!value) return null;
  const iso = value.includes("T") ? value : `${value.replace(" ", "T").replace(/(\.\d{3})\d+$/, "$1")}Z`;
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? null : date;
}

/** `Nov 3, 2024` */
export function shortDate(value: string | null | undefined): string {
  return parseGameDate(value)?.toLocaleDateString([], { dateStyle: "medium" }) ?? EMPTY;
}

/** `Nov 3, 23:01`; "Never" when there is no timestamp at all. */
export function dateTime(value: string | null | undefined): string {
  if (!value) return "Never";
  return parseGameDate(value)?.toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" }) ?? value;
}
