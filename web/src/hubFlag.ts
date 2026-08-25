/** ISO 3166-1 alpha-2 -> regional-indicator flag, so no emoji are hardcoded. */
export function flagEmoji(countryCode?: string | null): string {
  const cc = (countryCode || "").trim().toUpperCase();
  if (cc.length !== 2) return "";
  return String.fromCodePoint(
    ...[...cc].map((ch) => 0x1f1e6 + ch.charCodeAt(0) - 65)
  );
}

/** Hub IATA code prefixed with its country flag, when a country code is known. */
export function hubLabel(iata: string, countryCode?: string | null): string {
  const flag = flagEmoji(countryCode);
  return flag ? `${flag} ${iata}` : iata;
}
